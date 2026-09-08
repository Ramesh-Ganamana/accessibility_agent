"""Bounded initial navigation crawler; rich UI action exploration is Phase 2."""

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, Sequence

from accessibility_agent.config.settings import Settings
from accessibility_agent.crawler.fingerprint import digest, fingerprint
from accessibility_agent.crawler.navigation import classify_link
from accessibility_agent.crawler.readiness import ReadinessTimeout, wait_for_ready
from accessibility_agent.interfaces import BrowserSession
from accessibility_agent.models import Action, Element, Graph, Safety, State, UnscannedArea
from accessibility_agent.utils.privacy import Redactor
from accessibility_agent.utils.url_utils import normalize_url


class InitialCrawler:
    def __init__(self, settings: Settings, redactor: Redactor) -> None:
        self.settings, self.redactor = settings, redactor
        self.states: list[State] = []
        self.actions: list[Action] = []
        self.areas: list[UnscannedArea] = []
        self.urls_discovered: set[str] = set()
        self.urls_scanned: set[str] = set()
        self.tested_elements: set[tuple[str, str]] = set()

    def graph(self) -> Graph:
        return Graph(states=self.states, actions=self.actions)

    def unscanned(self) -> Sequence[UnscannedArea]:
        return self.areas

    def skip(
        self, url: str, reason: str, action: Action | None = None, *, detail: str = ""
    ) -> None:
        self.areas.append(
            UnscannedArea(
                url=self.redactor.url(url),
                reason=reason,
                detail=detail,
                retryable=reason == "page_readiness_timeout",
                action_id=action.action_id if action else None,
                state_id=action.source_state if action else None,
            )
        )
        if action:
            action.outcome = "skipped"
            action.reason = reason

    async def discover(self, session: BrowserSession) -> AsyncGenerator[State, None]:
        page = session.page
        root = normalize_url(page.url)
        queue: deque[tuple[str, int, Action | None]] = deque([(root, 0, None)])
        visited: dict[str, str] = {}
        seen_states: set[str] = set()
        self.urls_discovered.add(root)
        while queue:
            url, depth, via = queue.popleft()
            if url in visited:
                if via:
                    via.target_state, via.outcome = visited[url], "skipped"
                    via.reason = "already_visited"
                continue
            if len(self.states) >= self.settings.crawl.max_states:
                self.skip(url, "state_budget", via)
                continue
            if depth > self.settings.crawl.max_depth:
                self.skip(url, "depth_budget", via)
                continue
            try:
                if via is not None:
                    response = await session.navigate(
                        url, allow_risky=via.action_id in self.settings.crawl.allowed_action_ids
                    )
                    if response is not None and response.status >= 400:
                        self.skip(url, f"http_{response.status}", via)
                        via.outcome = "failed"
                        continue
                _, snapshot = await wait_for_ready(page, self.settings.crawl)
                if await page.locator('input[type="password"]:visible').count():
                    self.skip(url, "authentication_required", via)
                    continue
                state_id, dom_hash = fingerprint(page.url, snapshot)
                self.urls_discovered.add(normalize_url(page.url))
                if state_id in seen_states:
                    visited[url] = state_id
                    if via:
                        via.target_state, via.outcome = state_id, "executed"
                        self.tested_elements.add((via.source_state, via.element.element_id))
                    continue
                elements = [
                    Element(
                        element_id=digest([state_id, item["selector"]])[:16],
                        selector=item["selector"],
                        tag=item["tag"],
                        role=item["role"],
                        accessible_name=self.redactor.text(item["accessible_name"][:500]),
                        html=self.redactor.html(item["html"]),
                        attributes={
                            k: self.redactor.text(v) for k, v in item["attributes"].items()
                        },
                    )
                    for item in snapshot["elements"]
                ]
                state = State(
                    state_id=state_id,
                    url=self.redactor.url(page.url),
                    title=self.redactor.text(await page.title()),
                    dom_hash=dom_hash,
                    depth=depth,
                    visible_elements=elements,
                    interactive_elements=elements,
                    dialogs=snapshot["dialogs"],
                    menus=snapshot["menus"],
                    tabs=snapshot["tabs"],
                    forms=snapshot["forms"],
                )
                self.states.append(state)
                seen_states.add(state_id)
                visited[url] = state_id
                if via:
                    via.target_state, via.outcome = state_id, "executed"
                    self.tested_elements.add((via.source_state, via.element.element_id))
                for name in ("frames", "shadow", "truncated"):
                    if snapshot[name]:
                        self.skip(url, f"unsupported_{name}")
                for index, (item, element) in enumerate(
                    zip(snapshot["elements"], elements, strict=True)
                ):
                    target = item["href"]
                    action = Action(
                        action_id=digest([state_id, element.element_id])[:20],
                        source_state=state_id,
                        action_type="navigate" if target else "click",
                        element=element,
                    )
                    self.actions.append(action)
                    if not target:
                        self.skip(url, "interaction_requires_phase2", action)
                        continue
                    action.safety = classify_link(
                        target,
                        item["accessible_name"],
                        root,
                        self.settings.crawl.same_origin_only,
                        item["download"],
                    )
                    try:
                        target = normalize_url(target)
                        self.urls_discovered.add(target)
                    except ValueError:
                        self.skip(url, "unsupported_link_scheme", action)
                        continue
                    if action.safety != Safety.SAFE and not (
                        action.action_id in self.settings.crawl.allowed_action_ids
                        and action.safety in {Safety.CAUTION, Safety.DESTRUCTIVE}
                    ):
                        self.skip(target, f"policy_{action.safety.value.lower()}", action)
                    elif index >= self.settings.crawl.max_actions_per_state:
                        self.skip(target, "action_budget", action)
                    else:
                        queue.append((target, depth + 1, action))
                yield state
                if state.status == "scanned":
                    self.urls_scanned.add(normalize_url(page.url))
            except asyncio.CancelledError:
                self.skip(url, "duration_budget", via)
                for pending, _, pending_action in queue:
                    self.skip(pending, "duration_budget", pending_action)
                raise
            except ReadinessTimeout as error:
                self.skip(url, "page_readiness_timeout", via, detail=", ".join(error.reasons))
                if via:
                    via.outcome = "failed"
            except Exception as error:
                # Raw Playwright errors contain URLs, HTML, or filled values; never persist them.
                self.skip(url, "navigation_" + type(error).__name__, via)
                if via:
                    via.outcome = "failed"
