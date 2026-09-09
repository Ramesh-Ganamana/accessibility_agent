import asyncio
import re
from datetime import UTC, datetime, timedelta

from accessibility_agent.models import (
    Action,
    ApplicationInfo,
    Element,
    Evidence,
    Graph,
    Report,
    ScanMetadata,
    State,
    UnscannedArea,
)
from accessibility_agent.reporting.presentation import present_report
from accessibility_agent.reporting.writers import HTMLReportWriter


def state(number, status="scanned", url=None):
    return State(
        state_id=f"state-{number}", title=f"Page {number}", dom_hash=str(number),
        url=url or f"https://example.test/{number}", status=status,
    )


def report_for(states=(), actions=(), unscanned=(), evidence=()):
    started = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    return Report(
        application=ApplicationInfo(url="https://example.test/"),
        scan=ScanMetadata(
            scan_id="dashboard", started_at=started,
            completed_at=started + timedelta(seconds=125), status="completed",
        ),
        graph=Graph(states=list(states), actions=list(actions)),
        unscanned=list(unscanned), evidence=list(evidence),
    )


def test_dashboard_derived_urls_preserve_scanned_pages_and_failed_navigation(tmp_path):
    report = report_for(
        states=[state(1), state(2, "failed", "https://example.test/1")],
        unscanned=[
            UnscannedArea(url="https://example.test/1", reason="policy_unknown"),
            UnscannedArea(url="https://example.test/2", reason="page_readiness_timeout"),
            UnscannedArea(url="https://example.test/3", reason="http_503"),
            UnscannedArea(url="https://example.test/4", reason="policy_external"),
        ],
    )
    presentation = present_report(report, tmp_path)
    assert presentation.duration == "2m 5s"
    assert presentation.url_status_counts == {"scanned": 1, "failed": 2, "skipped": 1}
    assert presentation.crawl_rows[0].states == ["state-1", "state-2"]
    assert presentation.crawl_rows[0].reasons == ["policy_unknown"]
    assert presentation.state_status_counts == {"scanned": 1, "failed": 1}


def test_dashboard_bounds_graph_and_counts_interactions_without_navigation(tmp_path):
    states = [state(index) for index in range(40)]
    control = Element(element_id="save", selector="#save", tag="button", accessible_name="Save")
    actions = [
        Action(
            action_id=f"action-{index}", source_state="state-0", target_state="state-1",
            action_type="click", element=control, outcome="executed",
        )
        for index in range(74)
    ]
    actions.append(Action(
        action_id="navigate", source_state="state-1", target_state="state-2",
        action_type="navigate", element=control, outcome="executed",
    ))
    actions.append(Action(
        action_id="unverified", source_state="state-1", action_type="click", element=control,
        outcome="failed",
    ))
    report = report_for(states, actions)
    presentation = present_report(report, tmp_path)
    assert len(presentation.graph_nodes) == 36
    assert len(presentation.graph_edges) == 72
    assert (presentation.graph_omitted_states, presentation.graph_omitted_actions) == (4, 4)
    assert presentation.interaction_counts == {"executed": 74, "failed": 1}
    assert all(node.state_id in {s.state_id for s in states} for node in presentation.graph_nodes)
    assert presentation.graph_nodes == present_report(report, tmp_path).graph_nodes


def test_dashboard_offline_script_has_matching_nonce_and_escaped_data(tmp_path):
    observed = state(1)
    observed.title = '</script><script>alert("unsafe")</script>'
    observed.screenshot = "javascript:alert(1)"
    report = report_for(states=[observed], evidence=[Evidence(
        evidence_id="unsafe", state_id="state-1", kind="dom", path="%2e%2e/outside.json",
    )])
    html = asyncio.run(HTMLReportWriter().write(report, tmp_path)).read_text(encoding="utf-8")
    nonce = re.search(r"script-src 'nonce-([^']+)'", html).group(1)
    assert f'<script nonce="{nonce}">' in html
    assert html.count("<script ") == 1
    assert '</script><script>alert("unsafe")</script>' not in html
    assert "&lt;/script&gt;&lt;script&gt;" in html
    assert 'href="javascript:' not in html
    assert 'href="%2e%2e/outside.json"' not in html
    assert "<svg " in html
    assert 'id="state-state-1"' in html
    assert 'data-table-tools="crawl-urls"' in html
    for section in ("Overview", "Crawl Results", "Application States", "Interactions", "Failures",
                    "Screenshots / Evidence", "Crawl Graph"):
        assert section in html


def test_empty_dashboard_has_honest_empty_states(tmp_path):
    html = asyncio.run(HTMLReportWriter().write(report_for(), tmp_path)).read_text(encoding="utf-8")
    assert "No application states were observed." in html
    assert "No observed states are available to graph." in html
    assert "No detailed URL records are available" in html
    assert "Unique URLs in records" in html
