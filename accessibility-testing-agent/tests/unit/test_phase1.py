import asyncio
from datetime import UTC, datetime

from accessibility_agent.accessibility.axe_scanner import (
    convert,
    load_bundle,
    rule_tags,
    wcag_criteria,
)
from accessibility_agent.config.settings import Accessibility
from accessibility_agent.crawler.fingerprint import fingerprint
from accessibility_agent.crawler.navigation import classify_link
from accessibility_agent.findings.repository import MemoryFindingRepository
from accessibility_agent.models import ApplicationInfo, Report, ScanMetadata, State
from accessibility_agent.reporting.writers import HTMLReportWriter
from accessibility_agent.utils.privacy import Redactor
from accessibility_agent.utils.url_utils import normalize_url


def test_safety_and_normalization():
    assert normalize_url("https://EXAMPLE.com:443") == "https://example.com/"
    assert (
        classify_link("https://example.com/delete", "View", "https://example.com", True)
        == "DESTRUCTIVE"
    )
    assert classify_link("https://outside.test/", "Go", "https://example.com", True) == "EXTERNAL"
    assert classify_link("javascript:alert(1)", "Go", "https://example.com", True) == "UNKNOWN"
    assert (
        classify_link("https://example.com/orders", "Orders", "https://example.com", True) == "SAFE"
    )


def test_fingerprint_is_stable_and_state_sensitive():
    data = {"structure": [["button", "false"]], "text": "hello  world", "dialogs": [], "tabs": []}
    first = fingerprint("https://example.com", data)
    assert first == fingerprint("https://example.com/", {**data, "text": "hello world"})
    assert first != fingerprint("https://example.com", {**data, "dialogs": ["dialog"]})
    assert first != fingerprint("https://example.com", {**data, "ui": [["checkbox", True]]})
    assert first != fingerprint(
        "https://example.com", {**data, "form_values": [["select", "west"]]}
    )


def test_axe_mapping_bundle_and_incomplete_grouping():
    assert "axe" in load_bundle()
    assert wcag_criteria(["wcag111", "wcag2411", "wcag22aa"]) == ["1.1.1", "2.4.11"]
    assert "wcag22aa" in rule_tags(Accessibility())
    assert "wcag22aa" not in rule_tags(Accessibility(version="2.1"))
    raw = {
        "testEngine": {"version": "4.10.3"},
        "incomplete": [
            {
                "id": "color-contrast",
                "help": "Review contrast",
                "helpUrl": "https://dequeuniversity.com/rules/axe/",
                "description": "Contrast",
                "impact": "serious",
                "tags": ["wcag143"],
                "nodes": [{"html": '<input value="private">', "target": ["#field"]}],
            }
        ],
    }
    state = State(state_id="one", url="https://example.com/", title="", dom_hash="hash")
    result = convert(raw, state, Redactor([]))
    assert result.findings[0].classification == "NEEDS_REVIEW"
    assert "private" not in result.findings[0].occurrences[0].html
    repository = MemoryFindingRepository()
    repository.add(result.findings)
    state.state_id = "two"
    repository.add(convert(raw, state, Redactor([])).findings)
    assert len(repository.all()) == 1
    assert len(repository.all()[0].occurrences) == 2


def test_redaction():
    redactor = Redactor(["private-password"])
    assert "private-password" not in redactor.text("private-password token=abc")
    assert "abc" not in redactor.url("https://example.com/?token=abc")
    assert "cookie" not in redactor.html('<script>cookie</script><input value="secret">')
    assert "secret" not in redactor.html('<input value="secret">')


def test_html_escapes_page_content(tmp_path):
    report = Report(
        application=ApplicationInfo(url='<script>alert("unsafe")</script>'),
        scan=ScanMetadata(scan_id="test", started_at=datetime.now(UTC), status="failed"),
    )
    path = asyncio.run(HTMLReportWriter().write(report, tmp_path))
    html = path.read_text(encoding="utf-8")
    assert '<script>alert("unsafe")</script>' not in html
    assert "&lt;script&gt;" in html
