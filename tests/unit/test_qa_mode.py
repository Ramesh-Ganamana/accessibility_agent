import asyncio
import json
from datetime import UTC, datetime

import pytest

from accessibility_agent.config.settings import Crawl
from accessibility_agent.crawler.planner import safety
from accessibility_agent.main import main
from accessibility_agent.models import ApplicationInfo, Report, Safety, ScanMetadata
from accessibility_agent.reporting.writers import HTMLReportWriter


def control(**changes):
    return dict(
        tag="button",
        role="button",
        accessible_name="My card",
        href="",
        download=False,
        submit=False,
        input_type="button",
        attributes={},
        **changes,
    )


def test_testing_mode_is_explicit_and_broadens_labels_only():
    assert not Crawl().test_environment
    root = "https://example.test/"
    item = control()
    assert safety(item, root, Crawl()) == Safety.UNKNOWN
    assert safety(item, root, Crawl(test_environment=True)) == Safety.SAFE
    for changes, expected in [
        ({"tag": "div", "role": "", "clickable": True}, Safety.SAFE),
        ({"tag": "div", "role": "", "attributes": {"tabindex": "0"}}, Safety.UNKNOWN),
        ({"accessible_name": "Delete account"}, Safety.DESTRUCTIVE),
        ({"submit": True}, Safety.CAUTION),
        ({"download": True}, Safety.CAUTION),
        ({"input_type": "file"}, Safety.UNKNOWN),
        ({"navigation_hint": "https://outside.test/"}, Safety.EXTERNAL),
        ({"navigation_hint": "https://example.test/delete"}, Safety.DESTRUCTIVE),
    ]:
        assert safety(item | changes, root, Crawl(test_environment=True)) == expected


@pytest.mark.parametrize(
    "flag,enabled", [("--test-environment", True), ("--no-test-environment", False)]
)
def test_cli_testing_mode(flag, enabled, capsys):
    assert main(["validate-config", "--url", "https://example.test/", flag]) == 0
    assert json.loads(capsys.readouterr().out)["test_environment"] is enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_report_explains_active_policy(tmp_path, enabled):
    report = Report(
        application=ApplicationInfo(url="https://example.test/"),
        scan=ScanMetadata(scan_id="qa-policy", started_at=datetime.now(UTC), status="completed"),
        configuration={"test_environment": enabled, "crawl_mode": "interactive"},
    )
    html = asyncio.run(HTMLReportWriter().write(report, tmp_path)).read_text(encoding="utf-8")
    assert ('aria-label="Testing mode"' in html) is enabled
    assert ("Interaction policy: <strong>Conservative" in html) is not enabled
