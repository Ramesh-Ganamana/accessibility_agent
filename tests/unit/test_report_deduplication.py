import asyncio
from datetime import UTC, datetime

from accessibility_agent.findings.repository import MemoryFindingRepository
from accessibility_agent.models import (
    ApplicationInfo,
    Evidence,
    Finding,
    Occurrence,
    Report,
    ScanMetadata,
    ScanResult,
)
from accessibility_agent.reporting.presentation import present_report
from accessibility_agent.reporting.writers import HTMLReportWriter, JSONReportWriter


def issue(selector="#save", state="first", url="https://example.test/"):
    return Finding(
        id=f"{state}-button-name",
        title="Buttons must have discernible text",
        rule_id="button-name",
        rule_impact="serious",
        source="AXE",
        classification="VIOLATION",
        occurrences=[
            Occurrence(
                url=url,
                state_id=state,
                element="axe-node",
                selector=selector,
                target=[selector],
                html='<button class="icon"></button>',
                failure_summary="Element has no accessible name",
            )
        ],
    )


def report_for(findings, evidence=()):
    return Report(
        application=ApplicationInfo(url="https://example.test/"),
        scan=ScanMetadata(scan_id="dedup", started_at=datetime.now(UTC), status="completed"),
        findings=findings,
        evidence=list(evidence),
    )


def test_rule_group_retains_distinct_elements_pages_and_states():
    observations = [
        issue(),
        issue("#cancel"),
        issue(state="second"),
        issue(url="https://example.test/orders"),
    ]
    repository = MemoryFindingRepository()
    repository.add(observations)
    grouped = repository.all()
    assert len(grouped) == 1
    assert len(grouped[0].occurrences) == 4
    assert {node.selector for node in grouped[0].occurrences} == {"#save", "#cancel"}
    assert len({node.url for node in grouped[0].occurrences}) == 2
    assert observations[0].id == "first-button-name"  # Inputs stay unchanged.


def test_duplicate_observation_merges_evidence_without_duplicate_entries():
    first = issue()
    first.occurrences[0].evidence = ["dom"]
    repeated = first.model_copy(deep=True)
    repeated.occurrences[0].evidence = ["dom", "screenshot"]
    repository = MemoryFindingRepository()
    repository.add([first, repeated, repeated])
    occurrences = repository.all()[0].occurrences
    assert len(occurrences) == 1
    assert occurrences[0].evidence == ["dom", "screenshot"]
    assert first.occurrences[0].evidence == ["dom"]


def test_review_source_rule_and_impact_stay_distinct():
    base = issue()
    variants = [
        base,
        base.model_copy(update={"classification": "NEEDS_REVIEW"}),
        base.model_copy(update={"source": "DOM"}),
        base.model_copy(update={"rule_impact": "critical"}),
        base.model_copy(update={"rule_id": "other-rule"}),
    ]
    repository = MemoryFindingRepository()
    repository.add(variants)
    assert len(repository.all()) == 5


def test_html_counts_repeated_elements_pages_and_states_with_one_example(tmp_path):
    repeated = issue(state="second")
    changed = issue(state="third")
    changed.occurrences[0].failure_summary = "Accessible name contains only whitespace"
    findings = [
        issue(),
        repeated,
        issue("#cancel"),
        issue(url="https://example.test/orders"),
        changed,
    ]
    report = report_for(findings)
    presentation = present_report(report, tmp_path)
    assert len(presentation.issues) == 1
    group = presentation.issues[0]
    assert len(group.elements) == 1
    assert group.elements[0].states == ["first", "second", "third"]
    assert group.occurrence_count == 5
    assert (group.url_count, group.state_count) == (2, 3)
    html = asyncio.run(HTMLReportWriter().write(report, tmp_path)).read_text(encoding="utf-8")
    assert html.count('class="issue-group"') == 1
    assert html.count('class="affected-element"') == 1
    assert "5 occurrences" in html
    assert "2 affected pages" in html
    assert "Accessible name contains only whitespace" not in html


def test_same_issue_across_18_routes_shows_count_and_one_highlighted_example(tmp_path):
    findings = []
    for index in range(18):
        finding = issue(state=f"state-{index}", url=f"https://example.test/#/page/{index}")
        findings.append(finding)
    chosen = findings[8].occurrences[0]
    chosen.evidence = ["chosen-shot"]
    chosen.screenshot_marker = 7
    report = report_for(findings, [Evidence(
        evidence_id="chosen-shot", state_id=chosen.state_id, kind="screenshot",
        path="chosen.png", description="Annotated screenshot",
    )])
    before = report.model_dump_json()
    presentation = present_report(report, tmp_path)
    assert len(presentation.issues) == 1
    group = presentation.issues[0]
    assert (group.occurrence_count, group.url_count) == (18, 18)
    assert len(group.elements) == 1
    assert group.elements[0].occurrence == chosen
    reference = group.elements[0].screenshots[0]
    assert (reference.marker, reference.states) == (7, ["state-8"])
    html = asyncio.run(HTMLReportWriter().write(report, tmp_path)).read_text(encoding="utf-8")
    assert "18 occurrences" in html
    assert "18 affected pages" in html
    assert html.count('class="affected-element"') == 1
    assert html.count("Element has no accessible name") == 1
    assert html.count("<img ") == 1
    assert "affected element at marker #7" in html
    assert report.model_dump_json() == before


def test_screenshot_gallery_deduplicates_content_with_correct_markers_and_raw_json(tmp_path):
    (tmp_path / "screenshots").mkdir()
    # Equality of stored image bytes determines equality; similar-looking images are not merged.
    for name, content in (("one", b"identical"), ("two", b"identical"), ("three", b"different")):
        (tmp_path / "screenshots" / f"{name}.png").write_bytes(content)
    findings = []
    evidence = []
    for state, marker in (("one", None), ("two", 7), ("three", 2)):
        finding = issue(state=state)
        occurrence = finding.occurrences[0]
        occurrence.evidence = [f"{state}-shot"]
        occurrence.screenshot_marker = marker
        findings.append(finding)
        evidence.append(
            Evidence(
                evidence_id=f"{state}-shot",
                state_id=state,
                kind="screenshot",
                path=f"screenshots/{state}.png",
                description="Masked screenshot",
            )
        )
    report = report_for(findings, evidence)
    report.results = [
        ScanResult(
            state_id=f.occurrences[0].state_id, engine="axe", engine_version="test", findings=[f]
        )
        for f in findings
    ]
    before = report.model_dump_json()
    presentation = present_report(report, tmp_path)
    assert len(presentation.screenshots) == 1
    assert presentation.screenshots[0].states == {
        "one": "https://example.test/",
        "two": "https://example.test/",
    }
    references = presentation.issues[0].elements[0].screenshots
    assert [(r.screenshot.anchor, r.marker, r.states) for r in references] == [
        ("screenshot-1", 7, ["two"]),
    ]
    assert presentation.issues[0].occurrence_count == 3
    html = asyncio.run(HTMLReportWriter().write(report, tmp_path)).read_text(encoding="utf-8")
    assert html.count("<img ") == 1
    assert html.count('src="screenshots/one.png"') == 1
    assert 'src="screenshots/two.png"' not in html
    assert 'href="#screenshot-1"' in html
    assert "affected element at marker #7" in html
    json_path = asyncio.run(JSONReportWriter().write(report, tmp_path))
    restored = Report.model_validate_json(json_path.read_text(encoding="utf-8"))
    assert len(restored.results) == 3
    assert len(restored.evidence) == 3
    assert report.model_dump_json() == before


def test_target_chains_count_separately_and_untrusted_paths_are_not_read(tmp_path):
    first = issue()
    second = issue()
    second.occurrences[0].target = [["#shadow-host", "#save"]]
    report = report_for(
        [first, second],
        [
            Evidence(
                evidence_id="outside",
                state_id="first",
                kind="screenshot",
                path="../outside.png",
            )
        ],
    )
    presentation = present_report(report, tmp_path)
    assert len(presentation.issues[0].elements) == 1
    assert presentation.issues[0].occurrence_count == 2
    assert presentation.screenshots == []


def test_html_merges_impact_variants_without_merging_review_items_or_sources(tmp_path):
    first = issue()
    critical = issue(state="critical")
    critical.rule_impact = "critical"
    review = issue(state="review").model_copy(update={"classification": "NEEDS_REVIEW"})
    dom = issue(state="dom").model_copy(update={"source": "DOM"})
    report = report_for([first, critical, review, dom])
    presentation = present_report(report, tmp_path)
    assert len(presentation.issues) == 3
    assert presentation.issues[0].finding.rule_impact == "critical"
    assert presentation.issues[0].occurrence_count == 2
    assert presentation.severity_counts["critical"] == 1
    assert presentation.severity_counts["serious"] == 1
    assert first.rule_impact == "serious"


def test_html_duplicate_observation_does_not_inflate_occurrence_count(tmp_path):
    first = issue()
    repeated = first.model_copy(deep=True)
    repeated.occurrences[0].evidence = ["dom"]
    presentation = present_report(report_for([first, repeated]), tmp_path)
    assert presentation.issues[0].occurrence_count == 1


def test_representative_evidence_is_not_mixed_with_other_observations(tmp_path):
    first = issue()
    first.occurrences[0].evidence = ["first-dom"]
    second = issue("#cancel", state="second")
    second.occurrences[0].evidence = ["second-shot", "second-dom"]
    second.occurrences[0].screenshot_marker = 3
    evidence = [
        Evidence(evidence_id="first-dom", state_id="first", kind="dom", path="first.json"),
        Evidence(evidence_id="second-dom", state_id="second", kind="dom", path="second.json"),
        Evidence(
            evidence_id="second-shot", state_id="second", kind="screenshot", path="second.png"
        ),
    ]
    presentation = present_report(report_for([first, second], evidence), tmp_path)
    example = presentation.issues[0].elements[0]
    assert example.occurrence.selector == "#cancel"
    assert example.occurrence.state_id == "second"
    assert list(example.other_evidence) == ["second.json"]
    assert example.screenshots[0].marker == 3
    assert example.screenshots[0].states == ["second"]
