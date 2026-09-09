"""Real Chromium regressions for unfamiliar labels and independent scroll panes."""
# ruff: noqa: F811

import asyncio

import pytest
from test_readiness import configured, page, readiness_server  # noqa: F401

from accessibility_agent.agent.orchestrator import ScanOrchestrator
from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.crawler.discovery import inventory
from accessibility_agent.crawler.interactive import InteractiveCrawler
from accessibility_agent.utils.privacy import Redactor


@pytest.mark.parametrize("enabled", [False, True])
def test_unfamiliar_controls_use_existing_crawl_and_guards(readiness_server, tmp_path, enabled):
    url = readiness_server(
        page(
            '<a href="/existing">Existing link</a>'
            '<button type="button" aria-label="My card" '
            "onclick=\"content.textContent='Card details'\"><div>My card</div></button>"
            '<div role="button" tabindex="0" '
            "onclick=\"content.textContent='Card details'\">Claims</div>"
            "<span onclick=\"content.textContent='Card details'\">Rewards card</span>"
            "<button disabled>Disabled control</button><button>Delete account</button>"
            '<form><button type="submit">Continue</button></form>'
            '<input type="file" aria-label="Upload"><div tabindex="0">Focusable container</div>'
            '<div data-url="https://outside.invalid/">External destination</div>'
            '<section id="content">Rewards</section>'
        ),
        {"/existing": (0, "text/html", page("Link destination"))},
    )
    settings = configured(url, tmp_path, "interactive")
    settings.crawl.test_environment = enabled
    settings.crawl.max_actions = 25
    settings.crawl.max_states = 8
    settings.crawl.max_duration_seconds = 60
    settings.input_assistance.enabled = False
    report = asyncio.run(ScanOrchestrator().run(settings))
    actions = report.graph.actions
    for name in ("My card", "Claims", "Rewards card"):
        matches = [action for action in actions if action.element.accessible_name == name]
        assert matches
        if enabled:
            assert all(action.outcome == "executed" for action in matches)
            assert any(action.reason == "no_state_change" for action in matches)
        else:
            assert all(action.reason == "policy_unknown" for action in matches)
    for name, reason in {
        "Delete account": "policy_destructive",
        "Continue": "policy_caution",
        "Disabled control": "disabled_control",
        "Upload": "sensitive_control",
        "Focusable container": "policy_unknown",
        "External destination": "policy_external",
    }.items():
        matches = [action for action in actions if action.element.accessible_name == name]
        assert matches and all(action.reason == reason for action in matches)
    assert any(
        state.url == url + "existing" and state.status == "scanned" for state in report.graph.states
    )
    assert len(report.graph.states) == (3 if enabled else 2)
    assert report.configuration["test_environment"] is enabled
    html = (tmp_path / "accessibility-report.html").read_text(encoding="utf-8")
    assert ('aria-label="Testing mode"' in html) is enabled


def test_lazy_scroll_panels_are_bounded_restored_and_not_repeated(readiness_server, tmp_path):
    markup = page(
        "".join(
            f'<div class="pane" id="pane{i}" style="height:100px;width:150px;'
            'overflow-y:auto;display:inline-block"><div style="height:800px"></div></div>'
            for i in range(5)
        ),
        """window.maxScroll = {};
        document.querySelectorAll('.pane').forEach(p => p.addEventListener('scroll', () => {
          maxScroll[p.id] = Math.max(maxScroll[p.id] || 0, p.scrollTop);
          if(p.scrollTop > 50 && !p.querySelector('button')) {
            const b = document.createElement('button'); b.type='button';
            b.textContent='My card '+p.id; p.firstElementChild.prepend(b);
          }
        }));""",
    )
    url = readiness_server(markup)

    async def check():
        settings = configured(url, tmp_path, "interactive")
        session = ChromiumSession()
        await session.open(settings)
        try:
            await session.navigate(url)
            crawler = InteractiveCrawler(settings, Redactor([]))
            await crawler.scroll_for_discovery(session.page)
            data = await inventory(session.page)
            names = {item["accessible_name"] for item in data["elements"]}
            assert {f"My card pane{i}" for i in range(4)} <= names
            assert "My card pane4" not in names
            assert await session.page.evaluate(
                "() => [...document.querySelectorAll('.pane')].every(p=>p.scrollTop===0)"
            )
            maxima = await session.page.evaluate("() => maxScroll")
            assert len(maxima) == 4 and all(0 < value <= 300 for value in maxima.values())
            await session.page.evaluate("() => {window.maxScroll={}}")
            await crawler.scroll_for_discovery(session.page)
            await asyncio.sleep(0.1)
            assert all(
                value == 0 for value in (await session.page.evaluate("() => maxScroll")).values()
            )
        finally:
            await session.close()

    asyncio.run(check())
