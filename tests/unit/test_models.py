from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from accessibility_agent.models import (
    Action,
    ApplicationInfo,
    Coverage,
    Element,
    Finding,
    Graph,
    Occurrence,
    Report,
    ScanMetadata,
    State,
)


def state():
    return State(state_id="s1", url="https://example.com", title="Demo", dom_hash="abc")


def test_graph_references_and_duplicate_states():
    element = Element(element_id="e1", selector="button", tag="button")
    action = Action(
        action_id="a1", source_state="s1", target_state="s1", action_type="click", element=element
    )
    assert Graph(states=[state()], actions=[action]).actions[0].target_state == "s1"
    with pytest.raises(ValidationError):
        Graph(states=[state(), state()])
    with pytest.raises(ValidationError):
        Graph(actions=[action])


def test_ai_cannot_claim_violation():
    with pytest.raises(ValidationError):
        Finding(
            id="f1",
            title="AI",
            rule_id="context",
            source="AI",
            classification="VIOLATION",
            occurrences=[
                Occurrence(
                    url="https://example.com", state_id="s1", element="button", selector="button"
                )
            ],
        )


def test_impact_and_occurrences_preserved():
    occurrence = Occurrence(
        url="https://example.com", state_id="s1", element="button", selector="button"
    )
    finding = Finding(
        id="f1",
        title="Name",
        rule_id="button-name",
        source="AXE",
        classification="VIOLATION",
        rule_impact="serious",
        business_severity="critical",
        occurrences=[occurrence, occurrence],
    )
    assert finding.rule_impact == "serious"
    assert len(finding.occurrences) == 2
    assert Finding.model_validate_json(finding.model_dump_json()) == finding


def test_coverage_observed_denominator():
    assert Coverage().percentages()["states"] is None
    assert Coverage(states_discovered=4, states_scanned=3).percentages()["states"] == 75
    with pytest.raises(ValidationError):
        Coverage(states_discovered=1, states_scanned=2)


def test_report_roundtrip_and_independent_defaults():
    report = Report(
        application=ApplicationInfo(url="https://example.com"),
        scan=ScanMetadata(scan_id="run1", started_at=datetime.now(UTC), status="running"),
    )
    assert Report.model_validate_json(report.model_dump_json()) == report
    one, two = state(), state()
    one.dialogs.append("dialog")
    assert two.dialogs == []
