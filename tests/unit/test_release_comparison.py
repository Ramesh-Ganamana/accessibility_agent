"""Release changes require target-level evidence in equivalent tested UI states."""

import json
from datetime import UTC, datetime

import pytest

from accessibility_agent.conformance.comparison import compare_reports
from accessibility_agent.models import (
    Action,
    ApplicationInfo,
    Element,
    Finding,
    Graph,
    Occurrence,
    Report,
    RuleResult,
    ScanMetadata,
    ScanResult,
    State,
    UnscannedArea,
)

URL = "https://example.test/app?account=1#/settings"


def node(selector="#save", state="state", url=URL):
    return Occurrence(
        url=url, state_id=state, element="button", selector=selector, target=[selector]
    )


def report(scan_id, violations=("#save",), passes=(), *, state_id="state", dom="dom", url=URL):
    nodes = [node(selector, state_id, url) for selector in violations]
    findings = (
        [
            Finding(
                id=f"{scan_id}-button-name",
                title="Buttons need names",
                rule_id="button-name",
                source="AXE",
                classification="VIOLATION",
                occurrences=nodes,
            )
        ]
        if nodes
        else []
    )
    rules = []
    if nodes:
        rules.append(RuleResult(rule_id="button-name", outcome="violation", nodes=nodes))
    if passes:
        rules.append(
            RuleResult(
                rule_id="button-name",
                outcome="pass",
                nodes=[node(selector, state_id, url) for selector in passes],
            )
        )
    return Report(
        application=ApplicationInfo(url=url),
        scan=ScanMetadata(scan_id=scan_id, started_at=datetime.now(UTC), status="completed"),
        configuration={"browser": "chromium", "crawl_mode": "interactive"},
        graph=Graph(
            states=[
                State(
                    state_id=state_id,
                    url=url,
                    title="Settings",
                    dom_hash=dom,
                    status="scanned",
                )
            ]
        ),
        findings=findings,
        results=[
            ScanResult(
                state_id=state_id,
                engine="axe-core",
                engine_version="4.10.3",
                rules=rules,
                findings=[f.model_copy(deep=True) for f in findings],
            )
        ],
    )


def statuses(comparison):
    return {item["selector"]: item["status"] for item in comparison["items"]}


def test_self_comparison_and_unchanged_issue_use_stable_identity_not_scan_or_state_ids():
    baseline = report("baseline")
    original = baseline.model_dump_json()
    current = report("current", state_id="changed-state-id")
    for other in (baseline, current):
        result = compare_reports(baseline, other)
        assert result["counts"]["still_present"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["baseline_occurrences"] == 1
        assert result["items"][0]["current_occurrences"] == 1
        json.dumps(result)
    assert baseline.model_dump_json() == original


def test_exact_target_explicit_pass_can_resolve():
    result = compare_reports(report("old"), report("new", (), ("#save",)))
    assert statuses(result) == {"#save": "resolved"}


def test_explicit_pass_can_resolve_one_target_while_same_rule_violates_elsewhere():
    result = compare_reports(report("old"), report("new", ("#cancel",), ("#save",)))
    assert statuses(result) == {"#save": "resolved", "#cancel": "new"}


def test_changed_classification_does_not_clear_a_recorded_review_finding():
    baseline, current = report("old"), report("new", (), ("#save",))
    review = baseline.findings[0].model_copy(deep=True)
    review.classification = "NEEDS_REVIEW"
    current.findings.append(review)
    result = compare_reports(baseline, current)
    violation = next(i for i in result["items"] if i["classification"] == "VIOLATION")
    assert violation["status"] == "not_verified"


def test_missing_graph_context_cannot_resolve_even_when_rule_node_passes():
    baseline, current = report("old"), report("new", (), ("#save",))
    baseline.graph = Graph()
    current.graph = Graph()
    assert statuses(compare_reports(baseline, current)) == {"#save": "not_verified"}


def test_new_element_for_same_rule_stays_distinct_and_new_in_covered_state():
    result = compare_reports(report("old"), report("new", ("#save", "#cancel")))
    assert statuses(result) == {"#save": "still_present", "#cancel": "new"}


def test_selector_change_does_not_silently_resolve_old_element():
    result = compare_reports(report("old"), report("new", ("#renamed",)))
    assert statuses(result) == {"#save": "not_verified", "#renamed": "new"}
    assert "old target" in next(i for i in result["items"] if i["selector"] == "#save")["reason"]


def test_changed_dom_without_effective_state_evidence_is_unverified_even_with_pass():
    result = compare_reports(report("old"), report("new", (), ("#save",), dom="changed"))
    assert statuses(result) == {"#save": "not_verified"}


def test_repeated_observations_deduplicate_but_preserve_distinct_state_occurrences():
    baseline = report("old")
    baseline.findings.append(baseline.findings[0].model_copy(deep=True))
    repeated = report("old", state_id="repeated-state")
    baseline.graph.states.extend(repeated.graph.states)
    baseline.results.extend(repeated.results)
    baseline.findings.extend(repeated.findings)
    result = compare_reports(baseline, report("new"))
    assert len(result["items"]) == 1
    assert result["items"][0]["baseline_occurrences"] == 2
    assert result["items"][0]["current_occurrences"] == 1


@pytest.mark.parametrize("outcome", ["incomplete", "inapplicable", "pass", None])
def test_missing_target_or_missing_incomplete_rule_is_not_resolution(outcome):
    current = report("new", ())
    if outcome:
        current.results[0].rules = [RuleResult(rule_id="button-name", outcome=outcome)]
    assert statuses(compare_reports(report("old"), current)) == {"#save": "not_verified"}


@pytest.mark.parametrize("level", ["scan", "state", "unscanned"])
def test_failed_partial_or_omitted_coverage_cannot_resolve(level):
    current = report("new", (), ("#save",))
    if level == "scan":
        current.scan.status = "partial"
    elif level == "state":
        current.graph.states[0].status = "failed"
    else:
        current.unscanned.append(UnscannedArea(url=URL, state_id="state", reason="timeout"))
    assert statuses(compare_reports(report("old"), current)) == {"#save": "not_verified"}


@pytest.mark.parametrize("change", ["version", "engine", "settings", "wcag"])
def test_engine_version_and_settings_changes_prevent_resolution(change):
    current = report("new", (), ("#save",))
    if change == "version":
        current.results[0].engine_version = "5.0.0"
    elif change == "engine":
        current.results[0].engine = "another-engine"
    elif change == "settings":
        current.configuration["crawl_mode"] = "links"
    else:
        current.scan.wcag = "2.1-A"
    result = compare_reports(report("old"), current)
    assert statuses(result) == {"#save": "not_verified"}


@pytest.mark.parametrize(
    "url", [URL.replace("account=1", "account=2"), URL.replace("settings", "users")]
)
def test_meaningful_query_and_spa_fragment_are_part_of_identity(url):
    result = compare_reports(report("old"), report("new", url=url))
    assert result["counts"]["still_present"] == 0
    assert result["counts"]["not_verified"] == 1
    assert result["counts"]["newly_observed"] == 1


def test_equivalent_origin_spelling_and_serialized_selector_match():
    current = report("new", url=URL.replace("https://example.test", "https://EXAMPLE.test:443"))
    current.findings[0].occurrences[0].target = []
    current.findings[0].occurrences[0].selector = '["#save"]'
    result = compare_reports(report("old"), current)
    assert result["counts"]["still_present"] == 1
    assert len(result["items"]) == 1


def test_same_url_different_dialog_or_selected_tab_is_different_ui_state():
    baseline, current = report("old"), report("new", (), ("#save",))
    baseline.graph.states[0].dialogs = ["#edit-dialog"]
    current.graph.states[0].dialogs = ["#delete-dialog"]
    assert statuses(compare_reports(baseline, current)) == {"#save": "not_verified"}
    current.graph.states[0].dialogs = ["#edit-dialog"]
    current.graph.states[0].tabs = ["#advanced-tab"]
    assert statuses(compare_reports(baseline, current)) == {"#save": "not_verified"}


def add_replay(scan, selector, action_id):
    scan.graph.actions = [
        Action(
            action_id=action_id,
            source_state="state",
            target_state="state",
            action_type="click",
            element=Element(element_id=action_id, selector=selector, tag="button", role="button"),
            outcome="executed",
        )
    ]
    scan.graph.states[0].replay_actions = [action_id]


def test_replay_element_identity_matches_across_ids_but_different_actions_do_not():
    baseline, current = report("old"), report("new", (), ("#save",), dom="fixed-dom")
    add_replay(baseline, "#open-settings", "old-action")
    add_replay(current, "#open-settings", "new-action")
    assert statuses(compare_reports(baseline, current)) == {"#save": "resolved"}
    current.graph.actions[0].element.selector = "#open-help"
    assert statuses(compare_reports(baseline, current)) == {"#save": "not_verified"}


def test_unresolved_replay_reference_is_not_comparable_by_url():
    baseline, current = report("old"), report("new", (), ("#save",))
    baseline.graph.states[0].replay_actions = ["missing"]
    current.graph.states[0].replay_actions = ["missing"]
    assert statuses(compare_reports(baseline, current)) == {"#save": "not_verified"}


@pytest.mark.parametrize("coverage", ["rule", "state", "partial", "engine"])
def test_current_only_issue_without_comparable_baseline_is_newly_observed(coverage):
    baseline, current = report("old", (), ("#save",)), report("new")
    if coverage == "rule":
        baseline.results[0].rules = []
    elif coverage == "state":
        baseline.graph.states[0].dom_hash = "different-state"
    elif coverage == "partial":
        baseline.scan.status = "partial"
    else:
        baseline.results[0].engine_version = "older-engine"
    result = compare_reports(baseline, current)
    assert statuses(result) == {"#save": "newly_observed"}
    assert any("new coverage" in warning for warning in result["warnings"])


def test_classification_source_and_nested_target_are_distinct():
    baseline, current = report("old"), report("new")
    review = current.findings[0].model_copy(deep=True)
    review.classification = "NEEDS_REVIEW"
    dom = current.findings[0].model_copy(deep=True)
    dom.source = "DOM"
    nested = current.findings[0].model_copy(deep=True)
    nested.occurrences[0].target = [["#shadow-host", "#save"]]
    current.findings.extend([review, dom, nested])
    result = compare_reports(baseline, current)
    assert len(result["items"]) == 4
    assert result["counts"]["still_present"] == 1
    assert len({item["key"] for item in result["items"]}) == 4


def test_pairwise_output_does_not_claim_returned_history():
    result = compare_reports(report("old"), report("new"))
    assert "returned" not in result["counts"]
    assert any("multi-scan history" in warning for warning in result["warnings"])
