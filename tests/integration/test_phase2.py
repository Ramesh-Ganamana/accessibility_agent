import asyncio
import threading

import pytest

from accessibility_agent.agent.orchestrator import ScanOrchestrator
from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.config.settings import load_settings
from accessibility_agent.models import Report
from demo_app.server import make_server


@pytest.fixture
def ui_server():
    servers = []

    def start(markup=None):
        pages = {"/interactive": markup} if markup is not None else None
        server = make_server(
            "fixture@example.test", "fixture-password", landing="/interactive", pages=pages
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}/", server

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def configured(url, output):
    settings = load_settings(
        environ={
            "A11Y_URL": url,
            "A11Y_USERNAME": "fixture@example.test",
            "A11Y_PASSWORD": "fixture-password",
        }
    )
    settings.output_dir = output
    settings.crawl.timeout = 2000
    settings.crawl.settle_ms = 30
    settings.crawl.stability_timeout_ms = 800
    settings.crawl.dom_quiet_ms = 100
    settings.crawl.network_idle_ms = 100
    settings.crawl.max_states = 30
    settings.crawl.max_actions = 60
    settings.visual.enabled = False
    return settings


def test_interactive_end_to_end(ui_server, tmp_path):
    url, server = ui_server()
    settings = configured(url, tmp_path)
    settings.visual.enabled = True
    settings.crawl.form_values = {"#query": "private-form-fixture"}
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert report.authentication.status == "authenticated"
    assert report.coverage.states_scanned >= 14
    states = report.graph.states
    # Multiple real UI states at the very same URL, not merely a URL crawl.
    assert len({s.url for s in states}) == 1
    assert len({s.state_id for s in states}) == len(states)
    assert len(report.results) == len(states)
    assert any(s.dialogs for s in states)
    assert any(s.menus for s in states)
    assert any(s.tabs for s in states)
    assert any(s.forms for s in states)
    executed = [a for a in report.graph.actions if a.outcome == "executed"]
    names = {a.element.accessible_name for a in executed}
    assert {"Orders", "History", "Advanced fields", "Close dialog", "Blue"} <= names
    assert any(a.action_type == "select" for a in executed)
    assert any(a.action_type == "fill" for a in executed)
    assert any(a.reason == "duplicate_state" for a in executed)
    assert any(a.replay_count > 0 for a in report.graph.actions)
    assert all(a.outcome != "pending" for a in report.graph.actions)
    assert not any(u.reason == "replay_diverged" for u in report.unscanned)
    assert any(u.reason == "interaction_blocked" for u in report.unscanned)
    assert any(u.reason == "interaction_HTTPFailure" for u in report.unscanned)
    assert "POST /unsafe" not in server.request_paths
    assert "/delete" not in server.request_paths
    assert {"button-name", "label"} <= {f.rule_id for f in report.findings}
    assert any(len(f.occurrences) > 1 for f in report.findings)
    assert len([e for e in report.evidence if e.kind == "screenshot"]) == len(states)
    data = (tmp_path / "results.json").read_text(encoding="utf-8")
    assert Report.model_validate_json(data) == report
    for secret in ("private-form-fixture", "fixture-password", "private-session-fixture"):
        assert secret not in data
        for path in (tmp_path / "evidence").glob("*.json"):
            assert secret not in path.read_text(encoding="utf-8")
    assert report.coverage.keyboard_elements_tested == 0


SIMPLE = """<!doctype html><html lang="en"><title>Replay fixture</title><main>
<h1>Fixture</h1><button type="button" onclick="document.querySelector('#next').hidden=false;
this.hidden=true">Open next</button><section id="next" hidden>
<button type="button" onclick="this.insertAdjacentHTML('afterend','<input>');this.disabled=true">
Show fields</button></section></main></html>"""


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("max_states", 1, "state_budget"),
        ("max_depth", 0, "depth_budget"),
        ("max_actions", 1, "total_action_budget"),
        ("max_replay_steps", 0, "replay_budget"),
    ],
)
def test_interactive_budgets(ui_server, tmp_path, field, value, reason):
    url, _ = ui_server(SIMPLE)
    settings = configured(url, tmp_path)
    settings.authentication.success_selector = "h1"
    setattr(settings.crawl, field, value)
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert any(u.reason == reason for u in report.unscanned)
    assert report.scan.status == "partial"
    assert all(a.outcome != "pending" for a in report.graph.actions)


def test_replay_divergence_does_not_fabricate_target(ui_server, tmp_path):
    url, _ = ui_server(SIMPLE)
    settings = configured(url, tmp_path)
    settings.authentication.success_selector = "h1"

    class DivergingSession(ChromiumSession):
        async def checkpoint(self):
            await super().checkpoint()
            await self.page.evaluate(
                "document.querySelector('h1').textContent='Changed live state'"
            )

        async def restore(self):
            await super().restore()
            await self.page.add_init_script(
                "document.addEventListener('DOMContentLoaded',()=>{"
                "document.querySelector('h1').textContent='Changed server state'})"
            )

    report = asyncio.run(ScanOrchestrator(session=DivergingSession()).run(settings))
    assert report.coverage.states_scanned >= 3
    original = report.graph.states[0].state_id
    assert all(a.target_state is None for a in report.graph.actions if a.source_state == original)
    assert any(a.reason == "source_refreshed" for a in report.graph.actions)
    assert any(a.outcome == "executed" for a in report.graph.actions if a.source_state != original)


def test_allowed_unknown_action_is_terminal_and_never_replayed(ui_server, tmp_path):
    markup = """<!doctype html><html lang="en"><title>Allowance</title><main><h1>Allowance</h1>
    <button type="button" onclick="document.querySelector('main').innerHTML=
    '<h1>Created</h1><button>Open details</button>'">Create preview</button></main></html>"""
    url, _ = ui_server(markup)
    settings = configured(url, tmp_path / "first")
    settings.authentication.success_selector = "h1"
    first = asyncio.run(ScanOrchestrator().run(settings))
    action = next(a for a in first.graph.actions if a.element.accessible_name == "Create preview")
    assert action.outcome == "skipped"
    settings.crawl.allowed_action_ids = [action.action_id]
    settings.output_dir = tmp_path / "allowed"
    allowed = asyncio.run(ScanOrchestrator().run(settings))
    action = next(a for a in allowed.graph.actions if a.action_id == action.action_id)
    assert action.outcome == "executed" and not action.replayable and action.replay_count == 0
    assert allowed.coverage.states_scanned == 2
    assert any(u.reason == "non_replayable_path" for u in allowed.unscanned)


def test_busy_state_waits_for_async_content(ui_server, tmp_path):
    markup = """<!doctype html><html lang="en"><title>Async</title><main><h1>Async</h1>
    <button type="button" onclick="this.disabled=true;document.querySelector('main').setAttribute(
    'aria-busy','true');setTimeout(()=>{document.querySelector('main').innerHTML='<h1>Loaded</h1><input>';
    document.querySelector('main').removeAttribute('aria-busy')},150)">Open results</button>
    </main></html>"""
    url, _ = ui_server(markup)
    settings = configured(url, tmp_path)
    settings.authentication.success_selector = "h1"
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert report.coverage.states_scanned == 2
    assert any(f.rule_id == "label" for f in report.findings)
    assert not any(u.reason == "page_readiness_timeout" for u in report.unscanned)


def test_checkpoint_restores_storage_once(demo):
    async def run():
        settings = load_settings(environ={"A11Y_URL": demo[0] + "public"})
        session = ChromiumSession()
        await session.open(settings)
        try:
            await session.navigate(settings.application.url)
            await session.page.evaluate(
                "() => {localStorage.setItem('before','one');"
                "sessionStorage.setItem('before','two')}"
            )
            await session.checkpoint()
            await session.page.evaluate("() => {localStorage.clear();sessionStorage.clear()}")
            await session.restore()
            await session.navigate(settings.application.url)
            assert await session.page.evaluate("localStorage.getItem('before')") == "one"
            assert await session.page.evaluate("sessionStorage.getItem('before')") == "two"
            await session.page.evaluate("sessionStorage.setItem('later','three')")
            await session.navigate(settings.application.url)
            assert await session.page.evaluate("sessionStorage.getItem('later')") == "three"
        finally:
            await session.close()

    asyncio.run(run())


def test_ignored_clock_does_not_break_replay(ui_server, tmp_path):
    markup = SIMPLE.replace(
        "<h1>Fixture</h1>",
        '<h1>Fixture</h1><span id="clock"></span>'
        '<script>setInterval(()=>document.querySelector("#clock").textContent=performance.now(),10)</script>',
    )
    url, _ = ui_server(markup)
    settings = configured(url, tmp_path)
    settings.authentication.success_selector = "h1"
    settings.crawl.ignore_selectors = ["#clock"]
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert report.coverage.states_scanned == 3
    assert not any(
        u.reason in {"page_readiness_timeout", "replay_diverged"} for u in report.unscanned
    )


def test_allowed_http_write_is_not_replayed(ui_server, tmp_path):
    markup = """<!doctype html><html lang="en"><title>Write allowance</title><main><h1>Fixture</h1>
    <button type="button" onclick="fetch('/unsafe',{method:'POST'}).then(()=>{
    document.querySelector('main').innerHTML='<h1>Done</h1><button>Open details</button>'})">
    Show result</button></main></html>"""
    url, server = ui_server(markup)
    settings = configured(url, tmp_path)
    settings.authentication.success_selector = "h1"
    settings.crawl.allowed_request_urls = [url + "unsafe"]
    report = asyncio.run(ScanOrchestrator().run(settings))
    action = next(a for a in report.graph.actions if a.element.accessible_name == "Show result")
    assert action.outcome == "executed" and not action.replayable
    assert server.request_paths.count("POST /unsafe") == 1
    assert any(u.reason == "non_replayable_path" for u in report.unscanned)


def test_changing_entry_still_crawls_safe_hash_links(ui_server, tmp_path):
    markup = """<!doctype html><html lang="en"><title>Routes</title><main>
    <h1>Dashboard</h1><p id="changing"></p>
    <a href="/interactive#/one">Page one</a><a href="/interactive#/two">Page two</a>
    <a href="/delete">Delete everything</a>
    <button type="button"
    onclick="this.insertAdjacentHTML('afterend','<input>')">Open fields</button>
    </main><script>document.querySelector('#changing').textContent=Math.random()</script></html>"""
    url, server = ui_server(markup)
    settings = configured(url, tmp_path)
    settings.authentication.success_selector = "h1"
    settings.crawl.max_depth = 1
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert report.coverage.urls_scanned == 3
    assert {s.url.rsplit("#", 1)[-1] for s in report.graph.states} >= {"/one", "/two"}
    fallback = [a for a in report.graph.actions if a.reason == "direct_navigation"]
    assert len(fallback) == 2
    assert all(a.target_state for a in fallback)
    assert report.coverage.interactive_elements_tested >= 1
    assert "/delete" not in server.request_paths
    assert any(
        a.outcome == "executed" and a.element.accessible_name == "Open fields"
        for a in report.graph.actions
    )


def test_hidden_blank_links_and_background_posts_do_not_stop_crawl(ui_server, tmp_path):
    markup = """<!doctype html><html lang="en"><title>Discovery</title><main><h1>Links</h1>
    <nav hidden><a href="/interactive#/hidden">Hidden route</a></nav>
    <a href="/interactive#/blank" target="_blank">New tab route</a>
    <a href="/interactive#/blank">Duplicate route</a>
    <a href="/delete">Delete</a></main>
    <script>fetch('/unsafe',{method:'POST'}).catch(()=>{});
    if(location.hash==='#/blank') document.querySelector('main').insertAdjacentHTML(
      'beforeend','<a href="/interactive#/deep">Deeper route</a>');</script></html>"""
    url, server = ui_server(markup)
    settings = configured(url, tmp_path)
    settings.authentication.success_selector = "h1"
    settings.crawl.max_actions_per_state = 2
    settings.crawl.max_actions = 3
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert report.coverage.urls_scanned == 4
    assert any(s.url.endswith("#/deep") for s in report.graph.states)
    assert any(s.url.endswith("#/hidden") for s in report.graph.states)
    assert any(s.url.endswith("#/blank") for s in report.graph.states)
    assert any(a.reason == "duplicate_url" for a in report.graph.actions)
    assert not any(a.reason == "unsupported_popup" for a in report.graph.actions)
    assert any(u.reason == "navigation_background_requests_blocked" for u in report.unscanned)
    assert all(a.replayable for a in report.graph.actions if a.reason == "direct_navigation")
    assert all(e.visible for s in report.graph.states for e in s.interactive_elements)
    assert "/delete" not in server.request_paths
    # The initial document loads before crawl policy activation. Subsequent POSTs are blocked.
    assert server.request_paths.count("POST /unsafe") <= 1


def test_common_controls_are_exercised_without_starving_links(ui_server, tmp_path):
    markup = """<!doctype html><html lang="en"><title>Controls</title><main><h1>Controls</h1>
    <button type="button" onclick="document.querySelector('#panel').hidden=false">User menu</button>
    <section id="panel" hidden><button type="button"
      onclick="document.querySelector('#panel').hidden=true">Close menu</button>
      <input aria-label="Search" type="search"></section>
    <button type="button" onclick="this.setAttribute('aria-expanded','true');
      document.querySelector('#note').hidden=false;fetch('/unsafe',{method:'POST'}).catch(()=>{})">Notifications</button>
    <p id="note" hidden>Notifications panel</p>
    <button type="button" onclick="document.body.style.background='black';
      this.textContent='Switch to light mode'">Switch to dark mode</button>
    <div role="region" tabindex="-1">Notifications (F8)</div><ol tabindex="-1"></ol>
    <a href="/interactive#/one">First page</a><a href="/interactive#/two">Second page</a>
    <button type="button">Delete account</button><button type="submit">Submit payment</button>
    </main></html>"""
    url, server = ui_server(markup)
    settings = configured(url, tmp_path)
    settings.authentication.success_selector = "h1"
    settings.crawl.max_actions = 24
    report = asyncio.run(ScanOrchestrator().run(settings))
    executed = [
        a
        for a in report.graph.actions
        if a.outcome == "executed" and a.reason != "direct_navigation"
    ]
    assert {"User menu", "Notifications", "Switch to dark mode", "Close menu"} <= {
        a.element.accessible_name for a in executed
    }
    assert any(a.action_type == "fill" for a in executed)
    assert report.coverage.interactive_elements_tested >= 5
    assert report.coverage.urls_scanned == 3
    assert not any(
        e.tag in {"div", "ol"} for s in report.graph.states for e in s.interactive_elements
    )
    assert "POST /unsafe" not in server.request_paths
    assert any(u.reason == "interaction_background_requests_blocked" for u in report.unscanned)
    assert all(
        a.outcome == "skipped"
        for a in report.graph.actions
        if a.element.accessible_name in {"Delete account", "Submit payment"}
    )


@pytest.mark.parametrize("loading", ["<main>Loading module...</main>", "<main></main>"])
def test_delayed_application_is_ready_before_controls_are_planned(ui_server, tmp_path, loading):
    markup = (
        '<html lang="en"><title>Delayed module</title>'
        + loading
        + """<script>
    setTimeout(()=>{document.querySelector('main').innerHTML=`<h1>Ready</h1>
      <button type="button" onclick="this.textContent='Opened';
        document.querySelector('#panel').hidden=false">User menu</button>
      <section id="panel" hidden><input type="search" aria-label="Search"></section>`},700)
    </script></html>"""
    )
    url, _ = ui_server(markup)
    settings = configured(url, tmp_path)
    settings.authentication.success_selector = "main"
    settings.crawl.stability_timeout_ms = 2500
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert report.coverage.interactive_elements_tested >= 2
    assert report.graph.states[0].interactive_elements
    assert any(
        a.element.accessible_name == "User menu" and a.outcome == "executed"
        for a in report.graph.actions
    )


def test_loading_screen_never_claims_scanned_page(ui_server, tmp_path):
    url, _ = ui_server("<html><title>Loading</title><main>Loading module...</main></html>")
    settings = configured(url, tmp_path)
    settings.authentication.success_selector = "main"
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert report.coverage.urls_scanned == 0
    assert report.scan.status == "failed"
