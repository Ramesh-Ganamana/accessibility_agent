"""Resolve blocking application setup before scanning newly reached pages."""

import asyncio
import re
import threading
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from time import monotonic

from playwright.async_api import Error

from accessibility_agent.config.settings import Settings
from accessibility_agent.crawler.fingerprint import fingerprint
from accessibility_agent.crawler.readiness import ReadinessTimeout, state_is_current, wait_for_ready
from accessibility_agent.interaction.blockers import Blocker, detect_blockers
from accessibility_agent.interfaces import BrowserSession
from accessibility_agent.utils.privacy import Redactor

InputHandler = Callable[[list[Blocker]], Awaitable[bool]]


class InputAssistanceRequired(RuntimeError):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason, self.detail = reason, detail
        super().__init__(reason)


class InputCoordinator:
    def __init__(
        self, settings: Settings, redactor: Redactor, handler: InputHandler | None = None
    ) -> None:
        self.settings, self.redactor, self.handler = settings, redactor, handler
        self.prompts = 0

    async def prepare(
        self,
        session: BrowserSession,
        *,
        include_setup_forms: bool = False,
        watch_delayed: bool = True,
    ) -> bool:
        options = self.settings.input_assistance
        if not options.enabled:
            return False
        page = session.page
        ready = asyncio.create_task(wait_for_ready(page, self.settings.crawl))
        phase_started = monotonic()
        deadline = phase_started + self.settings.crawl.stability_timeout_ms / 1000
        quiet_since: float | None = None
        readiness_finished = False
        resolved_input = False
        pending_blockers: list[Blocker] | None = None
        blocker_since = phase_started

        async def finish() -> bool:
            if resolved_input:
                # Choices made after reaching a later URL must survive replay too.
                await session.checkpoint()
            return resolved_input

        try:
            while True:
                try:
                    blockers = await detect_blockers(
                        page,
                        options.blocking_selectors,
                        include_setup_forms=include_setup_forms,
                    )
                except Error as error:
                    if page.is_closed() or not any(
                        phrase in str(error).lower()
                        for phrase in ("execution context", "cannot find context", "navigation")
                    ):
                        raise
                    await asyncio.sleep(0.15)
                    if monotonic() >= deadline:
                        return await finish()
                    continue
                if blockers:
                    if blockers != pending_blockers:
                        pending_blockers = blockers
                        blocker_since = monotonic()
                    if (
                        (watch_delayed or resolved_input)
                        and monotonic() - blocker_since
                        < options.detection_wait_ms / 1000
                    ):
                        await asyncio.sleep(0.15)
                        continue
                    ready.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await ready
                    names = "; ".join(self.redactor.text(item.title)[:120] for item in blockers)
                    if self.handler is None or self.prompts >= options.max_prompts:
                        raise InputAssistanceRequired(
                            "user_input_required",
                            f"Setup needs user input: {names}. "
                            "Run with --assist-input and complete it in the browser.",
                        )
                    self.prompts += 1
                    try:
                        completed = await asyncio.wait_for(
                            self.handler(self._redacted(blockers)), options.timeout_seconds
                        )
                    except TimeoutError as error:
                        raise InputAssistanceRequired(
                            "user_input_timeout", "Timed out waiting for the user to finish setup."
                        ) from error
                    if not completed:
                        raise InputAssistanceRequired(
                            "user_input_cancelled",
                            "The user stopped before setup was complete.",
                        )
                    resolved_input = True

                    # The user has confirmed that they finished interacting with
                    # the browser. Do NOT assume the blocker is gone.
                    # Give the application time to update the DOM before checking again.
                    await asyncio.sleep(options.detection_wait_ms / 1000)

                    quiet_since = None
                    readiness_finished = False
                    pending_blockers = None
                    phase_started = monotonic()
                    deadline = (
                        phase_started + self.settings.crawl.stability_timeout_ms / 1000
                    )

                    ready = asyncio.create_task(
                        wait_for_ready(page, self.settings.crawl)
                    )
                    continue
                pending_blockers = None
                if not watch_delayed and not resolved_input:
                    # Replay visits already verified states. An immediate gate still
                    # matters, but repeating the delayed observation window for every
                    # restored branch would multiply crawl time.
                    return False
                elif not readiness_finished and ready.done():
                    try:
                        url, data = ready.result()
                    except ReadinessTimeout:
                        # The crawler owns the definitive readiness check. Continue
                        # watching for a delayed blocker, then let it retry readiness.
                        readiness_finished = True
                        quiet_since = phase_started
                        continue
                    except Error:
                        if page.is_closed() or monotonic() >= deadline:
                            raise
                        ready = asyncio.create_task(wait_for_ready(page, self.settings.crawl))
                        quiet_since = None
                        phase_started = monotonic()
                        deadline = (
                            phase_started + self.settings.crawl.stability_timeout_ms / 1000
                        )
                        await asyncio.sleep(0.15)
                        continue
                    try:
                        current = await state_is_current(
                            page,
                            self.settings.crawl,
                            fingerprint(url, data)[0],
                            bool(data.get("readiness_fallback")),
                        )
                    except Error as error:
                        if page.is_closed() or not any(
                            phrase in str(error).lower()
                            for phrase in ("execution context", "cannot find context", "navigation")
                        ):
                            raise
                        current = False
                    if not current:
                        if monotonic() >= deadline:
                            return await finish()
                        ready = asyncio.create_task(wait_for_ready(page, self.settings.crawl))
                        quiet_since = None
                    else:
                        quiet_since = quiet_since if quiet_since is not None else monotonic()
                        if monotonic() - quiet_since >= options.detection_wait_ms / 1000:
                            return await finish()
                elif readiness_finished:
                    if monotonic() - phase_started >= options.detection_wait_ms / 1000:
                        return await finish()
                if monotonic() >= deadline and not ready.done():
                    ready.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await ready
                    readiness_finished = True
                    quiet_since = phase_started
                await asyncio.sleep(0.15)
        finally:
            ready.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await ready

    def _redacted(self, blockers: list[Blocker]) -> list[Blocker]:
        return [
            replace(
                item,
                title=self.redactor.text(item.title),
                fields=tuple(
                    replace(
                        field,
                        label=self.redactor.text(field.label),
                        options=tuple(self.redactor.text(option) for option in field.options),
                    )
                    for field in item.fields
                ),
            )
            for item in blockers
        ]


async def console_input_handler(blockers: list[Blocker]) -> bool:
    """Use the application's own controls for arbitrary widgets and dependent choices."""

    def display(text: str) -> str:
        return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)[:180]

    print("\nScan paused: the application needs your input before crawling.", flush=True)
    for blocker in blockers:
        print("  " + display(blocker.title or "Required setup"), flush=True)
        for field in blocker.fields:
            suffix = " (required)" if field.required else ""
            print(f"    {display(field.label or field.kind)}{suffix}", flush=True)
            if field.options:
                print("      Choices: " + "; ".join(display(x) for x in field.options), flush=True)
    print(
        "Complete the required interaction in the Chromium window.\n"
        "Select the required option, fill any required fields, and click "
        "Continue/Confirm if the application requires it.\n"
        "IMPORTANT: Typing a value here does NOT select anything in the browser.\n"
        "After the browser dialog is completely finished, return here and press Enter.\n"
        "Type q to stop.",
        flush=True,
    )
    loop = asyncio.get_running_loop()
    answer: asyncio.Future[bool] = loop.create_future()

    def finish(value: bool) -> None:
        if not answer.done():
            answer.set_result(value)

    def read() -> None:
        try:
            value = input("Ready to continue: ").strip().lower() not in {"q", "quit", "cancel"}
        except (EOFError, OSError):
            value = False
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(finish, value)

    # A cancelled terminal read must not keep Python alive during report finalization.
    threading.Thread(target=read, daemon=True, name="a11y-user-input").start()
    return await answer
