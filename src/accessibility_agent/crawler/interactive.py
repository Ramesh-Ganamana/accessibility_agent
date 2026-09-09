"""Verified replay of UI state paths, with bounded breadth-first expansion."""

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary

from playwright.async_api import Page, Response

from accessibility_agent.crawler.crawler import InitialCrawler
from accessibility_agent.crawler.discovery import inventory
from accessibility_agent.crawler.fingerprint import digest, fingerprint
from accessibility_agent.crawler.navigation import classify_link
from accessibility_agent.crawler.planner import Step, identity, is_control, plan
from accessibility_agent.crawler.readiness import ReadinessTimeout, wait_for_ready
from accessibility_agent.interaction.coordinator import InputAssistanceRequired
from accessibility_agent.interfaces import BrowserSession
from accessibility_agent.models import Element, Safety, State
from accessibility_agent.utils.url_utils import normalize_url


class ReplayDiverged(RuntimeError):
    pass


class SourceChanged(ReplayDiverged):
    """A restored source must be inventoried and planned again before acting."""


class InteractionBlocked(RuntimeError):
    pass


class HTTPFailure(RuntimeError):
    pass


@dataclass
class Node:
    state: State
    path: tuple[Step, ...]


@dataclass
class Pending:
    source: Node
    step: Step


class InteractiveCrawler(InitialCrawler):
    async def scroll_for_discovery(self, page: Page) -> None:
        if not hasattr(self, "_scrolled"):
            self._scrolled: WeakKeyDictionary[Page, set[str]] = WeakKeyDictionary()
        urls = self._scrolled.setdefault(page, set())
        if page.url in urls:
            return
        urls.add(page.url)
        # Existing DOM controls below the fold are already inventoried and
        # Playwright scrolls to click them. This bounded pass also mounts lazy UI.
        position = await page.evaluate("() => [scrollX, scrollY]")
        try:
            for index in range(1, 4):
                moved = await page.evaluate(
                    "i => { const before=scrollY; "
                    "scrollTo({top:Math.min(i*innerHeight,document.documentElement.scrollHeight),"
                    "behavior:'instant'}); return scrollY!==before; }",
                    index,
                )
                if not moved:
                    break
                await asyncio.sleep(0.1)
        finally:
            if not page.is_closed():
                await page.evaluate(
                    "p => scrollTo({left:p[0],top:p[1],behavior:'instant'})", position
                )
        # Sidebar/content panes often scroll independently of the document.
        # Keep live node handles so no attributes are added to the scanned DOM.
        panels = await page.evaluate_handle(
            """ignored => [...document.querySelectorAll('body *')].slice(0,10000)
              .filter(e => {
                const s=getComputedStyle(e), r=e.getBoundingClientRect();
                return /^(auto|scroll|overlay)$/.test(s.overflowY) &&
                  e.clientHeight>=80 && e.scrollHeight>e.clientHeight+4 &&
                  r.width>0 && r.height>0 && s.visibility!=='hidden' &&
                  r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth &&
                  !e.closest('[inert],[aria-hidden="true"]') &&
                  !ignored.some(selector => e.matches(selector) || e.closest(selector));
              }).slice(0,4).map(element => ({element,
                top:element.scrollTop, left:element.scrollLeft}))""",
            self.settings.crawl.ignore_selectors,
        )
        try:
            for index in range(1, 4):
                moved = await panels.evaluate(
                    """(panels,i) => {
                      let moved=false;
                      for(const p of panels) {
                        if(!p.element.isConnected) continue;
                        const before=p.element.scrollTop;
                        p.element.scrollTo({top:p.top+i*p.element.clientHeight,
                          behavior:'instant'});
                        moved ||= p.element.scrollTop!==before;
                      }
                      return moved;
                    }""",
                    index,
                )
                if not moved:
                    break
                await asyncio.sleep(0.1)
        finally:
            try:
                if not page.is_closed():
                    await panels.evaluate(
                        """panels => panels.forEach(p => {
                          if(p.element.isConnected) p.element.scrollTo({
                            top:p.top,left:p.left,behavior:'instant'});
                        })"""
                    )
            finally:
                await panels.dispose()

    async def settled(
        self,
        session: BrowserSession,
        *,
        prepare: bool = False,
        watch_delayed: bool = True,
        inspect_after: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        if prepare:
            await self.prepare_page(session, watch_delayed)
        await self.scroll_for_discovery(session.page)
        url, data = await wait_for_ready(session.page, self.settings.crawl)
        if inspect_after and await self.prepare_page(session, False):
            url, data = await wait_for_ready(session.page, self.settings.crawl)
        return url, data

    def record(self, url: str, data: dict[str, Any], title: str, path: tuple[Step, ...]) -> State:
        state_id, dom_hash = fingerprint(url, data)
        elements = [
            Element(
                element_id=digest([state_id, item["selector"]])[:16],
                selector=item["selector"],
                tag=item["tag"],
                role=item["role"],
                accessible_name=self.redactor.text(item["accessible_name"][:500]),
                html=self.redactor.html(item["html"]),
                attributes={k: self.redactor.text(v) for k, v in item["attributes"].items()},
                visible=item.get("visible", True),
            )
            for item in data["elements"]
        ]
        interactive = [
            element
            for element, item in zip(elements, data["elements"], strict=True)
            if is_control(item) and element.visible
        ]
        state = State(
            state_id=state_id,
            url=self.redactor.url(url),
            title=self.redactor.text(title),
            dom_hash=dom_hash,
            fingerprint_version="2",
            depth=len(path),
            visible_elements=[e for e in elements if e.visible],
            discovered_links=[
                e for e, item in zip(elements, data["elements"], strict=True) if item["href"]
            ],
            interactive_elements=interactive,
            dialogs=data["dialogs"],
            menus=data["menus"],
            tabs=data["tabs"],
            forms=data["forms"],
            replay_actions=[step.action.action_id for step in path],
        )
        if fallback := data.get("readiness_fallback"):
            self.readiness_fallbacks[state_id] = fallback
        self.states.append(state)
        self.urls_discovered.add(normalize_url(url))
        for key in ("frames", "shadow", "truncated"):
            if data[key]:
                self.skip(url, "unsupported_" + key)
        return state

    async def perform(self, session: BrowserSession, step: Step) -> None:
        if step.direct_navigation:
            blocked_before = session.blocked_requests
            mutations_before = session.mutating_requests
            response = await session.navigate(step.url)
            if response is not None and response.status >= 400:
                raise HTTPFailure()
            await asyncio.sleep(self.settings.crawl.settle_ms / 1000)
            if session.blocked_requests != blocked_before:
                # Request guards remain active, but blocked background traffic does not
                # invalidate the document that was successfully loaded.
                self.skip(session.page.url, "navigation_background_requests_blocked")
            if session.mutating_requests != mutations_before:
                step.action.replayable = False
            return
        before = session.blocked_requests
        mutations_before = session.mutating_requests
        data = await inventory(session.page, self.settings.crawl.ignore_selectors)
        item = next(
            (e for e in data["elements"] if e["selector"] == step.action.element.selector), None
        )
        if item is None or identity(item) != step.expected_identity:
            raise ReplayDiverged("Target semantics changed")
        if item["disabled"] or item["inert"] or item["blocked_by_modal"]:
            raise ReplayDiverged("Target is not actionable")
        locator = session.page.locator(step.action.element.selector)
        action = step.action
        statuses: list[int] = []
        page = session.page

        def response_status(response: Response) -> None:
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                statuses.append(response.status)

        page.on("response", response_status)
        session.begin_interaction()
        try:
            if action.action_type == "select":
                if action.option_index is None:
                    raise ReplayDiverged("Missing option")
                await locator.select_option(index=action.option_index)
            elif action.action_type == "fill":
                if step.value is None:
                    raise ReplayDiverged("Missing test data")
                await locator.fill(step.value.get_secret_value())
            elif action.action_type == "navigate" and action.safety != Safety.SAFE:
                response = await session.navigate(step.url, allow_risky=True)
                if response is not None and response.status >= 400:
                    raise HTTPFailure()
            else:
                await locator.click()
            # Async handlers may begin after click/fill returns.
            await asyncio.sleep(max(self.settings.crawl.settle_ms / 1000, 0.2))
            popup_urls = await session.collect_popup_urls()
            if popup_urls:
                action.popup_count = max(action.popup_count, len(popup_urls))
                destination = popup_urls[0]
                action.popup_url = self.redactor.url(destination)
                for extra in popup_urls[1:]:
                    self.skip(extra, "popup_budget", detail="One popup per interaction is tested.")
                category = classify_link(
                    destination,
                    "",
                    self.settings.application.url,
                    self.settings.crawl.same_origin_only,
                )
                if category != Safety.SAFE:
                    self.skip(destination, "popup_policy_" + category.value.lower())
                else:
                    response = await session.navigate(destination)
                    if response is not None and response.status >= 400:
                        raise HTTPFailure()
            if session.blocked_requests != before:
                after = await inventory(page, self.settings.crawl.ignore_selectors)
                if fingerprint(page.url, after)[0] == fingerprint(page.url, data)[0]:
                    raise InteractionBlocked()
                self.skip(page.url, "interaction_background_requests_blocked")
            if session.mutating_requests != mutations_before:
                action.replayable = False
            if any(status >= 400 for status in statuses):
                raise HTTPFailure()
        finally:
            page.remove_listener("response", response_status)
            await session.collect_popup_urls()

    async def replay(
        self, session: BrowserSession, root_url: str, root_id: str, node: Node
    ) -> None:
        if len(node.path) > self.settings.crawl.max_replay_steps:
            raise ReplayDiverged("Replay length exceeds budget")
        await session.restore()
        if not node.path or not node.path[0].direct_navigation:
            response = await session.navigate(root_url)
            if response is not None and response.status >= 400:
                raise HTTPFailure()
            url, data = await self.settled(
                session, prepare=True, watch_delayed=False, inspect_after=True
            )
            expected: str | None = (
                node.state.state_id if not node.path else node.path[0].action.source_state
            )
            if fingerprint(url, data)[0] != expected:
                if not node.path and normalize_url(url) == normalize_url(root_url):
                    raise SourceChanged()
                raise ReplayDiverged("Entry state changed")
        for index, step in enumerate(node.path):
            if not step.action.replayable:
                raise ReplayDiverged("Action must not be replayed")
            before_url = session.page.url
            await self.perform(session, step)
            navigated = step.direct_navigation or normalize_url(session.page.url) != normalize_url(
                before_url
            )
            url, data = await self.settled(
                session,
                prepare=navigated,
                watch_delayed=False,
                inspect_after=True,
            )
            step.action.replay_count += 1
            expected = (
                node.state.state_id if index == len(node.path) - 1 else step.action.target_state
            )
            if fingerprint(url, data)[0] != expected:
                if len(node.path) == 1 and step.direct_navigation:
                    raise SourceChanged()
                raise ReplayDiverged("Intermediate state changed")

    async def discover(self, session: BrowserSession) -> AsyncGenerator[State, None]:
        try:
            root_url, root_data = await self.settled(session)
        except ReadinessTimeout as error:
            self.skip(session.page.url, "page_readiness_timeout", detail=", ".join(error.reasons))
            return
        await session.checkpoint()
        root_state = self.record(root_url, root_data, await session.page.title(), ())
        root_id = root_state.state_id
        nodes = {root_id: Node(root_state, ())}
        queue: deque[Pending] = deque()
        link_queue: deque[Pending] = deque()
        current: Pending | None = None
        attempts = 0
        links_since_interaction = 2
        scheduled_urls = {normalize_url(root_url)}
        url_states = {normalize_url(root_url): root_id}

        async def expand(node: Node, data: dict[str, Any]) -> None:
            steps = await plan(
                session.page, node.state, data["elements"], root_url, self.settings.crawl
            )
            steps.sort(key=lambda step: not step.direct_navigation)
            eligible = 0
            for step in steps:
                self.actions.append(step.action)
                if step.url:
                    try:
                        self.urls_discovered.add(normalize_url(step.url))
                    except ValueError:
                        pass
                reason = step.skip_reason
                if reason is None and step.direct_navigation:
                    target = normalize_url(step.url)
                    if target in scheduled_urls:
                        step.action.outcome = "skipped"
                        step.action.reason = "duplicate_url"
                        step.action.target_state = url_states.get(target)
                        continue
                if (
                    reason is None
                    and not step.direct_navigation
                    and any(not s.action.replayable for s in node.path)
                ):
                    reason = "non_replayable_path"
                if reason is None and eligible >= self.settings.crawl.max_actions_per_state:
                    reason = "action_budget"
                if reason is None and node.state.depth >= self.settings.crawl.max_depth:
                    reason = "depth_budget"
                if (
                    reason is None
                    and not step.direct_navigation
                    and len(node.path) > self.settings.crawl.max_replay_steps
                ):
                    reason = "replay_budget"
                if reason:
                    self.skip(step.url or node.state.url, reason, step.action)
                else:
                    eligible += 1
                    if step.direct_navigation:
                        scheduled_urls.add(normalize_url(step.url))
                    (link_queue if step.direct_navigation else queue).append(Pending(node, step))

        try:
            await expand(nodes[root_id], root_data)
            yield root_state
            if root_state.status == "scanned":
                self.urls_scanned.add(normalize_url(root_url))
            while link_queue or queue:
                if queue and (not link_queue or links_since_interaction >= 2):
                    current = queue.popleft()
                    links_since_interaction = 0
                else:
                    current = link_queue.popleft()
                    links_since_interaction += 1
                step = current.step
                if len(nodes) >= self.settings.crawl.max_states:
                    self.skip(step.url or current.source.state.url, "state_budget", step.action)
                    continue
                if attempts >= self.settings.crawl.max_actions:
                    self.skip(
                        step.url or current.source.state.url, "total_action_budget", step.action
                    )
                    continue
                attempts += 1
                try:
                    before_url = session.page.url
                    direct = step.direct_navigation
                    try:
                        if direct:
                            await session.restore()
                        else:
                            live = await inventory(
                                session.page, self.settings.crawl.ignore_selectors
                            )
                            if (
                                fingerprint(session.page.url, live)[0]
                                != current.source.state.state_id
                            ):
                                await self.replay(session, root_url, root_id, current.source)
                        await self.perform(session, step)
                    except SourceChanged:
                        if await session.page.locator('input[type="password"]:visible').count():
                            self.skip(
                                current.source.state.url, "authentication_required", step.action
                            )
                            continue
                        fresh_url, fresh_data = await self.settled(session, prepare=True)
                        fresh_id, _ = fingerprint(fresh_url, fresh_data)
                        step.action.outcome, step.action.reason = "skipped", "source_refreshed"
                        if fresh_id not in nodes:
                            fresh = self.record(
                                fresh_url,
                                fresh_data,
                                await session.page.title(),
                                current.source.path,
                            )
                            fresh.depth = current.source.state.depth
                            nodes[fresh_id] = Node(fresh, current.source.path)
                            await expand(nodes[fresh_id], fresh_data)
                            yield fresh
                            if fresh.status == "scanned":
                                self.urls_scanned.add(normalize_url(fresh_url))
                        continue
                    except ReplayDiverged:
                        # A changing dashboard must not block independently addressable links.
                        # Preserve strict source verification for all other UI actions.
                        if (
                            step.action.action_type != "navigate"
                            or step.action.safety != Safety.SAFE
                        ):
                            raise
                        await session.restore()
                        step.direct_navigation = True
                        await self.perform(session, step)
                        direct = True
                    navigated = direct or normalize_url(session.page.url) != normalize_url(
                        before_url
                    )
                    url, data = await self.settled(
                        session,
                        prepare=navigated,
                        inspect_after=not navigated,
                    )
                    if await session.page.locator('input[type="password"]:visible').count():
                        raise ReplayDiverged("Authentication required")
                    self.urls_discovered.add(normalize_url(url))
                    state_id, _ = fingerprint(url, data)
                    path = (step,) if direct else (*current.source.path, step)
                    if state_id not in nodes:
                        state = self.record(url, data, await session.page.title(), path)
                        state.depth = current.source.state.depth + 1
                        nodes[state_id] = Node(state, path)
                        new = True
                    else:
                        state, new = nodes[state_id].state, False
                    step.action.target_state = state_id
                    url_states[normalize_url(url)] = state_id
                    if direct:
                        url_states[normalize_url(step.url)] = state_id
                    step.action.outcome = "executed"
                    if direct:
                        step.action.reason = "direct_navigation"
                    if state_id == current.source.state.state_id:
                        step.action.reason = "no_state_change"
                    elif not new:
                        step.action.reason = "duplicate_state"
                    if not direct:
                        self.tested_elements.add(
                            (step.action.source_state, step.action.element.element_id)
                        )
                    if new:
                        await expand(nodes[state_id], data)
                        yield state
                    if state.status == "scanned":
                        self.urls_scanned.add(normalize_url(url))
                except InputAssistanceRequired:
                    raise
                except Exception as error:
                    reason = (
                        "page_readiness_timeout"
                        if isinstance(error, ReadinessTimeout)
                        else "replay_diverged"
                        if isinstance(error, ReplayDiverged)
                        else "interaction_blocked"
                        if isinstance(error, InteractionBlocked)
                        else "interaction_" + type(error).__name__
                    )
                    self.skip(
                        step.url or current.source.state.url,
                        reason,
                        step.action,
                        detail=", ".join(error.reasons)
                        if isinstance(error, ReadinessTimeout)
                        else "",
                    )
                    step.action.outcome = "failed"
        finally:
            if current and current.step.action.outcome == "pending":
                self.skip(current.source.state.url, "scan_interrupted", current.step.action)
            for pending in (*link_queue, *queue):
                self.skip(
                    pending.step.url or pending.source.state.url,
                    "scan_interrupted",
                    pending.step.action,
                )
