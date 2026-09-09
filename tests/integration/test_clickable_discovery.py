"""Incremental custom-control coverage with the same real crawler and scanner."""
# ruff: noqa: F811

import asyncio

import pytest
from playwright.async_api import async_playwright
from test_readiness import configured, page, readiness_server  # noqa: F401

from accessibility_agent.agent.orchestrator import ScanOrchestrator
from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.crawler.discovery import inventory


@pytest.mark.parametrize("test_environment", [False, True])
def test_hidden_href_less_anchors_do_not_stop_links_or_buttons(
    readiness_server, tmp_path, test_environment
):
    markup = page(
        '<nav hidden><a role="button" aria-label="Hidden button">Hidden button</a>'
        '<a tabindex="0" aria-label="Hidden focus target">Hidden focus target</a>'
        '<a role="link" aria-label="Hidden custom link">Hidden custom link</a>'
        '<a href="/hidden-route">Hidden real link</a></nav>'
        '<a href="/existing">Existing link</a>'
        '<a role="button" onclick="document.querySelector(\'#anchor-panel\').hidden=false">'
        'Show anchor panel</a>'
        '<button type="button" '
        'onclick="document.querySelector(\'#button-panel\').hidden=false">'
        'Open button panel</button>'
        '<section id="anchor-panel" hidden>Anchor details</section>'
        '<section id="button-panel" hidden>Button details</section>'
    )
    url = readiness_server(
        markup,
        {
            "/existing": (0, "text/html", page("Existing destination")),
            "/hidden-route": (0, "text/html", page("Hidden link destination")),
        },
    )
    settings = configured(url, tmp_path, "interactive")
    settings.input_assistance.enabled = False
    settings.crawl.test_environment = test_environment
    settings.crawl.max_depth = 1
    report = asyncio.run(ScanOrchestrator().run(settings))

    assert not any(area.reason == "scan_KeyError" for area in report.unscanned)
    assert report.coverage.urls_scanned == 3
    assert {state.url for state in report.graph.states if state.status == "scanned"} == {
        url,
        url + "existing",
        url + "hidden-route",
    }
    root = report.graph.states[0]
    actions = {
        action.element.accessible_name: action
        for action in report.graph.actions
        if action.source_state == root.state_id
    }
    for name in ("Existing link", "Hidden real link", "Show anchor panel", "Open button panel"):
        assert actions[name].outcome == "executed"
        assert actions[name].target_state is not None
    assert not {"Hidden button", "Hidden focus target", "Hidden custom link"} & actions.keys()


def test_custom_targets_links_popups_and_noop_share_existing_crawler(readiness_server, tmp_path):
    markup = page(
        '<a href="/existing">Existing link</a>'
        "<button onclick=\"document.querySelector('#panel').hidden=false\">Open panel</button>"
        "<div onclick=\"document.querySelector('#panel').hidden=false\">Show card details</div>"
        "<span onclick=\"document.querySelector('#panel').hidden=false\">View summary</span>"
        '<div role="button" tabindex="0" onclick="window.open(\'/popup\')">Open preview</div>'
        '<div data-href="/custom" onclick="location.href=this.dataset.href">Product card</div>'
        '<div style="cursor:pointer" id="pointer">Show delegated details</div>'
        "<div onclick=\"document.querySelector('#hidden').innerHTML='<i>noise</i>'\">"
        "Show nothing</div>"
        "<div onclick=\"document.body.dataset.unsafe='yes'\">Delete account</div>"
        "<span>Ordinary span</span><div>Ordinary div</div>"
        '<section id="panel" hidden>Details are open</section><div id="hidden" hidden></div>',
        "document.querySelector('#pointer').addEventListener('click', () => "
        "document.querySelector('#panel').hidden=false);",
    )
    url = readiness_server(
        markup,
        {
            "/existing": (0, "text/html", page("Existing destination")),
            "/custom": (0, "text/html", page("Custom destination")),
            "/popup": (0, "text/html", page("Popup destination")),
        },
    )
    settings = configured(url, tmp_path, "interactive")
    settings.input_assistance.enabled = False
    settings.crawl.max_states = 12
    settings.crawl.max_actions = 25
    settings.crawl.max_duration_seconds = 90
    settings.visual.enabled = True
    report = asyncio.run(ScanOrchestrator().run(settings))
    actions = {a.element.accessible_name: a for a in report.graph.actions}
    assert {url, url + "existing", url + "custom", url + "popup"} <= {
        state.url for state in report.graph.states if state.status == "scanned"
    }
    for name in ("Open panel", "Show card details", "View summary", "Show delegated details"):
        assert actions[name].outcome == "executed"
    assert actions["Show nothing"].reason == "no_state_change"
    assert actions["Delete account"].outcome == "skipped"
    assert "Ordinary span" not in actions and "Ordinary div" not in actions
    assert report.coverage.new_tabs_discovered >= 1
    assert actions["Open preview"].popup_url == url + "popup"
    assert len({s.state_id for s in report.graph.states}) == len(report.graph.states)
    assert len(list((tmp_path / "screenshots").glob("*.png"))) == len(report.results)

    async def check_dashboard():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                dashboard = await browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                dashboard.on("pageerror", lambda error: errors.append(str(error)))
                await dashboard.goto((tmp_path / "accessibility-report.html").as_uri())
                tools = dashboard.locator('[data-table-tools="crawl-urls"]')
                search = tools.locator("input")
                await search.fill("no-matching-url")
                assert await dashboard.locator("#crawl-urls tbody tr:visible").count() == 0
                await search.fill("/popup")
                assert await dashboard.locator("#crawl-urls tbody tr:visible").count() == 1
                await search.fill("")
                await tools.locator("select").select_option("scanned")
                rows = dashboard.locator("#crawl-urls tbody tr:visible")
                assert await rows.count() >= 4
                await dashboard.locator("#crawl-urls th").first.locator("button").click()
                assert (
                    await dashboard.locator("#crawl-urls th").first.get_attribute("aria-sort")
                    == "ascending"
                )
                target = "state-" + report.graph.states[0].state_id
                await dashboard.evaluate("id => location.hash=id", target)
                await dashboard.wait_for_function(
                    "id => document.getElementById(id).querySelector('details').open", arg=target
                )
                await dashboard.evaluate(
                    "() => {history.replaceState(null,'','#overview'); "
                    "scrollTo({top:0,behavior:'instant'})}"
                )
                await dashboard.screenshot(path=str(tmp_path / "dashboard-preview.png"))
                await dashboard.set_viewport_size({"width": 390, "height": 844})
                assert await dashboard.evaluate(
                    "() => document.documentElement.scrollWidth <= innerWidth"
                )
                assert not errors
            finally:
                await browser.close()

    asyncio.run(check_dashboard())


def test_custom_controls_below_fold_and_nested_pointer_are_discovered(readiness_server, tmp_path):
    url = readiness_server(
        page(
            '<div role="main"><div id="card" style="cursor:pointer">'
            '<span>View card</span></div></div><div style="height:1300px"></div>'
            "<div onclick=\"this.textContent='Details'\">Show lower details</div>"
        )
    )

    async def check():
        session = ChromiumSession()
        await session.open(configured(url, tmp_path, "interactive"))
        try:
            await session.navigate(url)
            data = await inventory(session.page)
            custom = [item for item in data["elements"] if item.get("clickable")]
            assert [item["accessible_name"] for item in custom] == [
                "View card",
                "Show lower details",
            ]
        finally:
            await session.close()

    asyncio.run(check())


def test_external_popup_is_counted_without_being_crawled(readiness_server, tmp_path):
    url = readiness_server(
        page(
            "<button onclick=\"window.open('https://outside.invalid/private')\">"
            "Open external</button>"
        )
    )
    settings = configured(url, tmp_path, "interactive")
    settings.input_assistance.enabled = False
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert report.coverage.new_tabs_discovered == 1
    assert {state.url for state in report.graph.states} == {url}
    assert any(area.reason == "popup_policy_external" for area in report.unscanned)


def test_popup_destination_is_captured_before_navigation(readiness_server, tmp_path):
    url = readiness_server(page("<button onclick=\"window.open('/popup')\">Open popup</button>"))

    async def check():
        session = ChromiumSession()
        await session.open(configured(url, tmp_path, "interactive"))
        try:
            await session.navigate(url)
            session.enable_crawl_policy()
            session.begin_interaction()
            await session.page.get_by_role("button", name="Open popup").click()
            await asyncio.sleep(0.2)
            assert await session.collect_popup_urls() == [url + "popup"]
        finally:
            await session.close()

    asyncio.run(check())
