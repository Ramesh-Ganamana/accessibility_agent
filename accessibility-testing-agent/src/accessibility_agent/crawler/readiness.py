"""Bounded, shared document/network/render readiness before discovering a state."""

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from weakref import WeakKeyDictionary

from playwright.async_api import Page, Request
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from accessibility_agent.config.settings import Crawl
from accessibility_agent.crawler.discovery import inventory
from accessibility_agent.crawler.fingerprint import digest, fingerprint


class ReadinessTimeout(RuntimeError):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("Page did not become ready: " + ", ".join(reasons))


@dataclass
class NetworkActivity:
    pending: set[Request] = field(default_factory=set)
    last_activity: float = field(default_factory=monotonic)

    def started(self, request: Request) -> None:
        # Streaming connections have no document-completion signal. UI stability
        # and an optional application ready selector still apply to their output.
        if request.resource_type in {"websocket", "eventsource"}:
            return
        self.pending.add(request)
        self.last_activity = monotonic()

    def finished(self, request: Request) -> None:
        if request in self.pending:
            self.pending.remove(request)
            self.last_activity = monotonic()


_NETWORK: WeakKeyDictionary[Page, NetworkActivity] = WeakKeyDictionary()


def monitor_page(page: Page) -> NetworkActivity:
    """Attach before navigation so early application requests are not missed."""
    if page not in _NETWORK:
        activity = NetworkActivity()
        _NETWORK[page] = activity
        page.on("request", activity.started)
        page.on("requestfinished", activity.finished)
        page.on("requestfailed", activity.finished)

        def closed(closed_page: Page) -> None:
            _NETWORK.pop(closed_page, None)

        page.on("close", closed)
    return _NETWORK[page]


async def wait_for_ready(page: Page, settings: Crawl) -> tuple[str, dict[str, Any]]:
    network = monitor_page(page)
    reasons = ["document_load"]
    try:
        async with asyncio.timeout(settings.stability_timeout_ms / 1000):
            await page.wait_for_load_state("load", timeout=settings.stability_timeout_ms)
            await asyncio.sleep(settings.settle_ms / 1000)
            previous = ""
            stable_since = monotonic()
            while True:
                data = await inventory(
                    page,
                    settings.ignore_selectors,
                    loading_selectors=settings.loading_selectors,
                    ready_selector=settings.ready_selector,
                    check_readiness=True,
                )
                ready = data["readiness"]
                signature = digest([fingerprint(page.url, data)[0], ready["layout"]])
                now = monotonic()
                reasons = []
                for condition, reason in (
                    (ready["document_complete"], "document_load"),
                    (not network.pending, "pending_requests"),
                    (
                        now - network.last_activity >= settings.network_idle_ms / 1000,
                        "network_idle",
                    ),
                    (ready["fonts_loaded"], "fonts"),
                    (ready["images_loaded"], "images"),
                    (not data["busy"], "loading_indicators"),
                    (data["render_ready"], "empty_document"),
                    (ready["application_ready"], "ready_selector"),
                ):
                    if not condition:
                        reasons.append(reason)
                if signature != previous or reasons:
                    stable_since = now
                if now - stable_since < settings.dom_quiet_ms / 1000:
                    reasons.append("dom_ui_stability")
                if not reasons:
                    return page.url, data
                previous = signature
                await asyncio.sleep(0.1)
    except (TimeoutError, PlaywrightTimeoutError) as error:
        raise ReadinessTimeout(reasons) from error


async def state_is_current(page: Page, settings: Crawl, state_id: str) -> bool:
    """Cheap final guard; discovery already waited for the full quiet window."""
    data = await inventory(
        page,
        settings.ignore_selectors,
        loading_selectors=settings.loading_selectors,
        ready_selector=settings.ready_selector,
        check_readiness=True,
    )
    ready = data["readiness"]
    return bool(
        not monitor_page(page).pending
        and ready["document_complete"]
        and ready["fonts_loaded"]
        and ready["images_loaded"]
        and ready["application_ready"]
        and data["render_ready"]
        and not data["busy"]
        and fingerprint(page.url, data)[0] == state_id
    )
