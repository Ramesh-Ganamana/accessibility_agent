"""Chromium lifecycle and same-origin navigation guard."""

import json
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Error,
    Page,
    Playwright,
    Response,
    Route,
    StorageState,
    async_playwright,
)

from accessibility_agent.authentication.redirects import authorization_origin
from accessibility_agent.config.settings import Settings
from accessibility_agent.crawler.navigation import classify_link
from accessibility_agent.crawler.readiness import monitor_page
from accessibility_agent.models import Safety
from accessibility_agent.utils.url_utils import normalize_url, origin


class ChromiumSession:
    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cdp: CDPSession | None = None
        self.blocked_navigation = False
        self._crawl_mode = False
        self._allowed_url: str | None = None
        self._settings: Settings | None = None
        self._checkpoint: StorageState | None = None
        self._session_storage: dict[str, str] = {}
        self._entry_origin = ""
        self._blocked_requests = 0
        self._mutating_requests = 0
        self._restore_script_id: str | None = None
        self._authentication_origins: set[str] = set()
        self._blocked_auth_origin = ""

    @property
    def authentication_origins(self) -> list[str]:
        return sorted(self._authentication_origins)

    @property
    def blocked_auth_origin(self) -> str:
        return self._blocked_auth_origin

    @property
    def blocked_requests(self) -> int:
        return self._blocked_requests

    @property
    def mutating_requests(self) -> int:
        return self._mutating_requests

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser session is not open")
        return self._page

    async def open(self, settings: Settings) -> None:
        self._settings = settings
        self._authentication_origins = set(settings.authentication.allowed_origins)
        self._blocked_auth_origin = ""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=settings.browser.headless)
        await self._new_context(settings)

    async def _new_context(self, settings: Settings) -> None:
        if self._browser is None:
            raise RuntimeError("Browser is not open")
        self._context = await self._browser.new_context(
            viewport={
                "width": settings.browser.viewport.width,
                "height": settings.browser.viewport.height,
            },
            accept_downloads=False,
            # service_workers="block",
            storage_state=self._checkpoint,
        )
        restore_script: str | None = None
        if self._checkpoint is not None:
            payload = json.dumps({"origin": self._entry_origin, "values": self._session_storage})
            restore_script = (
                """(() => {
                const checkpoint = """
                + payload
                + """;
                if (location.origin === checkpoint.origin) {
                    sessionStorage.clear();
                    for (const [key, value] of Object.entries(checkpoint.values)) {
                        sessionStorage.setItem(key, value);
                    }
                }
            })();"""
            )
        expected = origin(settings.application.url)

        def blocked(url: str, method: str, main_document: bool) -> bool:
            try:
                if not self._crawl_mode and method == "GET" and main_document:
                    discovered = authorization_origin(url, settings.application.url)
                    if discovered:
                        self._authentication_origins.add(discovered)
                auth_origins = {origin(item) for item in self._authentication_origins}

                cross_origin = origin(url) != expected and (
                        origin(url) not in auth_origins
                        and (
                                (self._crawl_mode and settings.crawl.same_origin_only)
                                or not self._crawl_mode
                        )
                )
                if cross_origin and not self._crawl_mode and main_document:
                    parts = urlsplit(normalize_url(url))
                    self._blocked_auth_origin = f"{parts.scheme}://{parts.netloc}"
                risky = (
                    self._crawl_mode
                    and classify_link(url, "", settings.application.url, False) != Safety.SAFE
                    and normalize_url(url) != self._allowed_url
                )
                return cross_origin or risky
            except ValueError:
                return True

        async def auxiliary_guard(route: Route) -> None:
            request = route.request
            unsafe_method = (
                self._crawl_mode
                and settings.crawl.mode == "interactive"
                and request.method not in {"GET", "HEAD", "OPTIONS"}
                and request.url not in settings.crawl.allowed_request_urls
            )
            if unsafe_method:
                self._blocked_requests += 1
                await route.abort("blockedbyclient")
            elif request.is_navigation_request() and (
                request.frame.page != self.page or request.frame != self.page.main_frame
            ):
                self._blocked_requests += 1
                await route.abort("blockedbyclient")
            else:
                await route.continue_()

        await self._context.route("**/*", auxiliary_guard)
        self._page = await self._context.new_page()
        monitor_page(self.page)
        cdp = await self._context.new_cdp_session(self.page)
        self._cdp = cdp
        frame_tree = await cdp.send("Page.getFrameTree")
        main_frame_id = frame_tree["frameTree"]["frame"]["id"]

        async def guard_document(payload: dict[str, Any]) -> None:
            request_id = payload["requestId"]
            try:
                # CDP emits every redirect hop, before it is sent to the server.
                request = payload["request"]
                mutating = (
                    self._crawl_mode
                    and settings.crawl.mode == "interactive"
                    and request["method"] not in {"GET", "HEAD", "OPTIONS"}
                )
                prohibited_method = (
                    mutating and request["url"] not in settings.crawl.allowed_request_urls
                )
                if (
                    payload["resourceType"] == "Document"
                    and blocked(
                        request["url"], request["method"], payload.get("frameId") == main_frame_id
                    )
                ) or prohibited_method:
                    self.blocked_navigation = True
                    self._blocked_requests += 1
                    await cdp.send(
                        "Fetch.failRequest",
                        {"requestId": request_id, "errorReason": "BlockedByClient"},
                    )
                else:
                    if mutating:
                        self._mutating_requests += 1
                    await cdp.send("Fetch.continueRequest", {"requestId": request_id})
            except Error:
                pass  # Closing the page cancels outstanding requests; do not log payloads.

        cdp.on("Fetch.requestPaused", guard_document)
        await cdp.send(
            "Fetch.enable",
            {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
        )
        if restore_script is not None:
            await cdp.send("Page.enable")
            result = await cdp.send(
                "Page.addScriptToEvaluateOnNewDocument", {"source": restore_script}
            )
            self._restore_script_id = result["identifier"]
        self.page.set_default_timeout(settings.crawl.timeout)
        self.page.set_default_navigation_timeout(settings.crawl.timeout)
        self.page.on("dialog", lambda dialog: dialog.dismiss())

    def enable_crawl_policy(self) -> None:
        self._crawl_mode = True

    async def checkpoint(self) -> None:
        if self._context is None:
            raise RuntimeError("Browser is not open")
        self._checkpoint = await self._context.storage_state(indexed_db=True)
        self._session_storage = await self.page.evaluate(
            "() => Object.fromEntries(Object.entries(sessionStorage))"
        )
        parts = urlsplit(self.page.url)
        self._entry_origin = f"{parts.scheme}://{parts.netloc}"

    async def restore(self) -> None:
        if self._checkpoint is None or self._settings is None or self._context is None:
            raise RuntimeError("Checkpoint is not available")
        await self._context.close()
        await self._new_context(self._settings)

    async def navigate(self, url: str, *, allow_risky: bool = False) -> Response | None:
        self._allowed_url = normalize_url(url) if allow_risky else None
        try:
            return await self.page.goto(url, wait_until="domcontentloaded")
        finally:
            self._allowed_url = None
            if self._restore_script_id is not None and self._cdp is not None:
                await self._cdp.send(
                    "Page.removeScriptToEvaluateOnNewDocument",
                    {"identifier": self._restore_script_id},
                )
                self._restore_script_id = None

    async def close(self) -> None:
        try:
            if self._context:
                await self._context.close()
        finally:
            try:
                if self._browser:
                    await self._browser.close()
            finally:
                if self._playwright:
                    await self._playwright.stop()
