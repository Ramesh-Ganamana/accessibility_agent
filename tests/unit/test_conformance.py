from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from accessibility_agent.conformance.builder import (
    build_acr,
    compare_assessments,
    prepare_review,
)
from accessibility_agent.conformance.models import CriterionReview, Product, ReviewSet
from accessibility_agent.models import (
    ApplicationInfo,
    Finding,
    Occurrence,
    Report,
    RuleResult,
    ScanMetadata,
    ScanResult,
)


def sample_report(wcag="2.2-AA", outcome="violation"):
    report = Report(
        application=ApplicationInfo(url="https://example.test/"),
        scan=ScanMetadata(
            scan_id="scan-one", started_at=datetime(2026, 9, 9, tzinfo=UTC),
            status="completed", wcag=wcag,
        ),
        results=[ScanResult(
            state_id="state-one", engine="axe-core", engine_version="4.10.3",
            rules=[RuleResult(rule_id="button-name", outcome=outcome, tags=["wcag412"])],
        )],
    )
    if outcome == "violation":
        report.findings = [Finding(
            id="button", title="Buttons need names", rule_id="button-name",
            source="AXE", classification="VIOLATION", wcag=["4.1.2"],
            occurrences=[Occurrence(
                url="https://example.test/", state_id="state-one", element="button",
                selector="#notifications",
            )],
        )]
    return report


def product():
    return Product(
        application_id="demo-qa-admin", name="Demo", version="1.0", scope="Dashboard",
        environment="QA", user_role="Administrator",
    )


def decision(criterion_id="4.1.2", value="Partially Supports"):
    return CriterionReview(
        criterion_id=criterion_id, conformance=value, reviewer="Fixture reviewer",
        reviewed_on=date(2026, 9, 9), methods="Keyboard and screen-reader walkthrough",
        remarks="The notification button is affected.", evidence=["Review ticket TEST-1"],
    )


def test_automated_observations_never_become_conformance():
    for outcome in ("violation", "incomplete", "pass", "inapplicable"):
        report = sample_report(outcome=outcome)
        draft = build_acr(report, prepare_review(report, product()))
        assert len(draft.criteria) == draft.pending_count == 55
        assert all(row.review.conformance is None for row in draft.criteria)
        row = next(row for row in draft.criteria if row.criterion_id == "4.1.2")
        assert row.observations[outcome] == 1
        assert row.checklist and row.url.startswith("https://www.w3.org/")
        untouched = next(row for row in draft.criteria if row.criterion_id == "1.2.1")
        assert untouched.automated_status == "not_tested"


def test_review_is_bound_to_exact_source_and_scope():
    report = sample_report()
    review = prepare_review(report, product())
    report.scan.scan_id = "different"
    with pytest.raises(ValueError, match="different or modified"):
        build_acr(report, review)
    report.scan.scan_id = "scan-one"
    report.application.title = "Changed report content"
    with pytest.raises(ValueError, match="different or modified"):
        build_acr(report, review)
    report.application.title = ""
    review.level = "A"
    with pytest.raises(ValueError, match="scope"):
        build_acr(report, review)


def test_manual_decisions_require_attribution_and_evidence():
    with pytest.raises(ValidationError):
        CriterionReview(criterion_id="4.1.2", conformance="Supports")
    for field in ("reviewer", "methods", "remarks", "reviewed_on", "evidence"):
        data = decision().model_dump()
        data[field] = None if field == "reviewed_on" else [] if field == "evidence" else " "
        with pytest.raises(ValidationError):
            CriterionReview.model_validate(data)


def test_unknown_duplicate_or_missing_review_criteria_are_rejected():
    report = sample_report()
    review = prepare_review(report, product())
    data = review.model_dump()
    data["criteria"].append(data["criteria"][0])
    with pytest.raises(ValidationError):
        ReviewSet.model_validate(data)
    review.criteria.pop()
    with pytest.raises(ValueError, match="every selected"):
        build_acr(report, review)
    review.criteria.append(CriterionReview(criterion_id="9.9.9"))
    with pytest.raises(ValueError, match="every selected"):
        build_acr(report, review)


def test_conflicting_support_decision_does_not_hide_violation():
    report = sample_report()
    review = prepare_review(report, product())
    review.criteria = [decision(value="Supports") if row.criterion_id == "4.1.2" else row
                       for row in review.criteria]
    draft = build_acr(report, review)
    row = next(row for row in draft.criteria if row.criterion_id == "4.1.2")
    assert draft.kind == "DRAFT_ACR" and draft.conflict_count == 1
    assert row.conflicts and row.findings[0].finding_id == "button"
    assert row.review.conformance == "Supports"  # Attributed claim, not an accepted scanner result.


def test_not_evaluated_only_allowed_for_aaa():
    report = sample_report(wcag="2.2-AAA")
    review = prepare_review(report, product())
    review.criteria = [decision("1.2.6", "Not Evaluated") if row.criterion_id == "1.2.6" else row
                       for row in review.criteria]
    assert build_acr(report, review).pending_count == 85
    review.criteria = [decision("4.1.2", "Not Evaluated") if row.criterion_id == "4.1.2" else row
                       for row in review.criteria]
    with pytest.raises(ValueError, match="only for Level AAA"):
        build_acr(report, review)


def test_findings_outside_wcag_are_not_lost():
    report = sample_report()
    report.findings[0].wcag = []
    draft = build_acr(report, prepare_review(report, product()))
    assert draft.unmapped_finding_ids == ["button"]


def test_assessment_comparison_requires_same_application_and_coverage_scope():
    report = sample_report(outcome="pass")
    review = prepare_review(report, product())
    old = build_acr(report, review)
    new = old.model_copy(deep=True)
    changes = compare_assessments(old, new)
    assert all(change["status"] == "not_verified" for change in changes)
    new.product.application_id = "another-app"
    with pytest.raises(ValueError, match="application IDs"):
        compare_assessments(old, new)
    new.product.application_id = old.product.application_id
    new.product.user_role = "Employee"
    with pytest.raises(ValueError, match="user role"):
        compare_assessments(old, new)


def test_reviewed_assessment_change_is_not_called_automatically_fixed():
    report = sample_report(outcome="pass")
    reviews = prepare_review(report, product())
    reviews.criteria = [decision() if row.criterion_id == "4.1.2" else row
                        for row in reviews.criteria]
    old = build_acr(report, reviews)
    reviews.criteria = [decision(value="Supports") if row.criterion_id == "4.1.2" else row
                        for row in reviews.criteria]
    new = build_acr(report, reviews)
    change = next(row for row in compare_assessments(old, new) if row["criterion_id"] == "4.1.2")
    assert change["status"] == "assessment_changed"


def test_product_identity_cannot_be_whitespace():
    data = product().model_dump()
    data["scope"] = " "
    with pytest.raises(ValidationError):
        Product.model_validate(data)
