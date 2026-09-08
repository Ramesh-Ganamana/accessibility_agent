import asyncio

from test_readiness import DOMSpyScanner, configured, page, readiness_server  # noqa: F401

from accessibility_agent.agent.orchestrator import ScanOrchestrator
from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.models import ScanResult


def test_late_dom_change_before_scanner_is_untested(readiness_server, tmp_path):  # noqa: F811
    url = readiness_server(page("<p>Initial data</p>"))
    settings = configured(url, tmp_path, "interactive")

    class LateRenderSession(ChromiumSession):
        async def checkpoint(self):
            await super().checkpoint()
            await self.page.evaluate("document.querySelector('main').textContent='Updated data'")

    scanner = DOMSpyScanner()
    report = asyncio.run(
        ScanOrchestrator(session=LateRenderSession(), scanner=scanner).run(settings)
    )
    assert not scanner.observations
    assert not report.results
    assert report.coverage.states_scanned == 0
    assert any(area.reason == "page_changed_before_scan" for area in report.unscanned)


def test_loading_during_scanner_discards_result(readiness_server, tmp_path):  # noqa: F811
    url = readiness_server(page("<p>Initial data</p>"))
    settings = configured(url, tmp_path, "links")

    class LateRenderScanner:
        async def scan(self, session, state):
            await session.page.evaluate(
                "document.querySelector('main').setAttribute('aria-busy','true')"
            )
            return ScanResult(state_id=state.state_id, engine="slow-fixture", engine_version="1")

    report = asyncio.run(ScanOrchestrator(scanner=LateRenderScanner()).run(settings))
    assert not report.results
    assert report.coverage.states_scanned == 0
    assert any(area.reason == "page_changed_during_scan" for area in report.unscanned)
