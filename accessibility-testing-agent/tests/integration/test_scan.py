import asyncio
import json
import os
import subprocess
import sys

from accessibility_agent.accessibility.axe_scanner import AxeScanner
from accessibility_agent.agent.orchestrator import ScanOrchestrator
from accessibility_agent.config.settings import load_settings
from accessibility_agent.models import Report
from accessibility_agent.utils.privacy import Redactor


def settings_for(url, directory):
    settings = load_settings(
        environ={
            "A11Y_URL": url,
            "A11Y_USERNAME": "fixture@example.test",
            "A11Y_PASSWORD": "fixture-only-password",
        }
    )
    settings.output_dir = directory
    settings.crawl.mode = "links"  # Phase 1 regression suite remains explicit.
    settings.crawl.timeout = 4000
    settings.crawl.max_states = 5
    settings.crawl.settle_ms = 50
    return settings


def test_real_authenticated_scan(demo, tmp_path):
    url, server = demo
    report = asyncio.run(ScanOrchestrator().run(settings_for(url, tmp_path)))
    assert report.authentication.status == "authenticated"
    assert report.scan.status == "partial"  # intentional 500 and external redirect
    assert report.coverage.states_scanned == 2
    assert {"image-alt", "button-name", "label", "color-contrast"} <= {
        finding.rule_id for finding in report.findings
    }
    assert any(rule.outcome == "pass" for result in report.results for rule in result.rules)
    assert any(area.reason == "policy_destructive" for area in report.unscanned)
    assert any(area.reason == "policy_external" for area in report.unscanned)
    assert any(area.reason == "http_500" for area in report.unscanned)
    assert any(area.reason.startswith("navigation_") for area in report.unscanned)
    assert "/delete" not in server.request_paths
    assert len(report.graph.states) == 2
    assert all(action.outcome != "pending" for action in report.graph.actions)
    assert report.coverage.keyboard_elements_tested == 0
    assert report.coverage.accessibility_checks_executed > 0
    assert len([e for e in report.evidence if e.kind == "screenshot"]) == 2
    for evidence in report.evidence:
        assert (tmp_path / evidence.path).is_file()
    result_text = (tmp_path / "results.json").read_text(encoding="utf-8")
    assert Report.model_validate_json(result_text) == report
    assert "fixture-only-password" not in result_text
    assert "demo_session" not in result_text
    assert "fixture@example.test" not in result_text
    assert "NEEDS_REVIEW" in result_text or any(
        r.outcome == "inapplicable" for s in report.results for r in s.rules
    )
    assert "At a glance" in (tmp_path / "accessibility-report.html").read_text()


def test_invalid_login_produces_failed_report(demo, tmp_path):
    settings = settings_for(demo[0], tmp_path)
    settings.authentication.password = "wrong"  # Pydantic converts to SecretStr
    settings.crawl.timeout = 600
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert report.scan.status == "failed"
    assert report.authentication.status == "failed"
    assert report.coverage.states_scanned == 0
    assert (tmp_path / "results.json").exists()
    assert len(report.graph.states) == 0


def test_state_budget_is_reported(demo, tmp_path):
    settings = settings_for(demo[0], tmp_path)
    settings.crawl.max_states = 1
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert report.coverage.states_scanned == 1
    assert report.scan.status == "partial"
    assert any(area.reason == "state_budget" for area in report.unscanned)


def test_scanner_failure_does_not_stop_next_page(demo, tmp_path):
    settings = settings_for(demo[0], tmp_path)
    delegate = AxeScanner(settings.accessibility, Redactor([]), 4000)

    class FailFirst:
        def __init__(self):
            self.count = 0

        async def scan(self, session, state):
            self.count += 1
            if self.count == 1:
                raise RuntimeError("secret exception must not be copied")
            return await delegate.scan(session, state)

    report = asyncio.run(ScanOrchestrator(scanner=FailFirst()).run(settings))
    assert report.coverage.states_scanned == 1
    assert len(report.graph.states) == 2
    assert any(area.reason == "scanner_RuntimeError" for area in report.unscanned)
    assert "secret exception" not in report.model_dump_json()


def test_cli_real_scan(demo, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("crawl:\n  max_states: 1\n  timeout: 4000\nvisual: {enabled: false}\n")
    env = {
        **os.environ,
        "A11Y_URL": demo[0],
        "A11Y_USERNAME": "fixture@example.test",
        "A11Y_PASSWORD": "fixture-only-password",
        "AI_ENABLED": "false",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "accessibility_agent",
            "scan",
            "--config",
            str(config),
            "--output",
            str(tmp_path / "report"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert "1 states scanned" in result.stdout
    assert "fixture-only-password" not in result.stderr + result.stdout
    assert json.loads((tmp_path / "report/results.json").read_text())["scan"]["status"] == "partial"


def test_public_scan_completes(demo, tmp_path):
    settings = load_settings(environ={"A11Y_URL": demo[0] + "public"})
    settings.output_dir = tmp_path
    report = asyncio.run(ScanOrchestrator().run(settings))
    assert report.scan.status == "completed"
    assert report.authentication.status == "not_required"
    assert report.coverage.states_scanned == 1


def test_duration_timeout_finalizes_report(demo, tmp_path):
    settings = settings_for(demo[0], tmp_path)
    settings.crawl.max_duration_seconds = 3

    class SlowScanner:
        async def scan(self, session, state):
            await asyncio.sleep(30)

    report = asyncio.run(ScanOrchestrator(scanner=SlowScanner()).run(settings))
    assert report.scan.status == "failed"
    assert any(area.reason == "duration_budget" for area in report.unscanned)
    assert all(action.outcome != "pending" for action in report.graph.actions)
    assert report.scan.completed_at is not None
