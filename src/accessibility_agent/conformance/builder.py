"""Build draft reporting worksheets without deriving conformance from scan passes."""

import hashlib
from collections import Counter
from datetime import UTC, datetime

from accessibility_agent.accessibility.axe_scanner import wcag_criteria
from accessibility_agent.conformance.catalog import criteria_for, manual_review_guidance
from accessibility_agent.conformance.models import (
    CriterionAssessment,
    DraftACR,
    FindingReference,
    Product,
    ReviewSet,
)
from accessibility_agent.models import Report


def report_digest(report: Report) -> str:
    """Bind reviews to the entire validated scan, not merely a user-controlled scan ID."""
    return hashlib.sha256(report.model_dump_json().encode("utf-8")).hexdigest()


def prepare_review(report: Report, product: Product) -> ReviewSet:
    version, separator, level = report.scan.wcag.partition("-")
    if not separator:
        raise ValueError("Scan has no supported WCAG version and level")
    catalog = criteria_for(version, level)
    return ReviewSet.model_validate({
        "source_scan_id": report.scan.scan_id,
        "source_sha256": report_digest(report),
        "product": product.model_dump(mode="json"),
        "wcag_version": version,
        "level": level,
        "criteria": [{"criterion_id": criterion.id} for criterion in catalog],
    })


def build_acr(report: Report, reviews: ReviewSet) -> DraftACR:
    if (reviews.source_scan_id != report.scan.scan_id
            or reviews.source_sha256 != report_digest(report)):
        raise ValueError("Review belongs to a different or modified scan; prepare a fresh review")
    if f"{reviews.wcag_version}-{reviews.level}" != report.scan.wcag:
        raise ValueError("Review scope does not match the scan WCAG target")
    catalog = criteria_for(reviews.wcag_version, reviews.level)
    expected = {criterion.id for criterion in catalog}
    by_id = {review.criterion_id: review for review in reviews.criteria}
    if expected != set(by_id):
        raise ValueError("Review must contain every selected criterion exactly once")

    # A rule is an automated observation, not proof of a complete success criterion.
    observations: dict[str, Counter[str]] = {key: Counter() for key in expected}
    findings: dict[str, list[FindingReference]] = {key: [] for key in expected}
    for result in report.results:
        for rule in result.rules:
            for criterion_id in wcag_criteria(rule.tags):
                if criterion_id in observations:
                    observations[criterion_id][rule.outcome] += 1
    unmapped: list[str] = []
    for finding in report.findings:
        mapped = (set(finding.wcag) | set(wcag_criteria(finding.tags))) & expected
        if not mapped:
            unmapped.append(finding.id)
        reference = FindingReference(
            finding_id=finding.id, rule_id=finding.rule_id, title=finding.title,
            classification=finding.classification, source=finding.source,
            occurrence_count=len(finding.occurrences),
            urls=sorted({occurrence.url for occurrence in finding.occurrences}),
            selectors=sorted({occurrence.selector for occurrence in finding.occurrences}),
            evidence_ids=sorted({
                evidence for occurrence in finding.occurrences for evidence in occurrence.evidence
            }),
        )
        for criterion_id in mapped:
            findings[criterion_id].append(reference)

    rows = []
    for criterion in catalog:
        review = by_id[criterion.id]
        if review.conformance == "Not Evaluated" and criterion.level != "AAA":
            raise ValueError("Not Evaluated is permitted only for Level AAA; leave others pending")
        counts = observations[criterion.id]
        references = findings[criterion.id]
        violation = bool(counts["violation"]) or any(
            finding.classification == "VIOLATION" for finding in references
        )
        incomplete = bool(counts["incomplete"]) or any(
            finding.classification in {"NEEDS_REVIEW", "AI_INFERENCE"} for finding in references
        )
        conflicts = []
        if violation and review.conformance in {"Supports", "Not Applicable"}:
            conflicts.append(
                "The reviewer decision conflicts with recorded violations. Investigate or retest "
                "before publishing; this draft does not discard the scanner evidence."
            )
        rows.append(CriterionAssessment(
            criterion_id=criterion.id, title=criterion.title, level=criterion.level,
            url=criterion.url,
            automated_status=("violations" if violation else "needs_review" if incomplete
                              else "observations_only" if counts else "not_tested"),
            observations=dict(counts), findings=references,
            checklist=list(manual_review_guidance(criterion)), review=review, conflicts=conflicts,
        ))

    methods = sorted({
        f"Automated {result.engine} {result.engine_version}" for result in report.results
    })
    if report.configuration.get("keyboard_executed"):
        methods.append("Recorded automated keyboard checks; human keyboard review is still needed")
    methods.extend(sorted({
        "Reviewer-reported: " + review.methods for review in reviews.criteria
        if review.conformance is not None
    }))
    limitations = [
        "Draft preparation worksheet, not a completed VPAT, certification or legal assurance. "
        "The product owner must verify scope, evidence, remarks and all applicable criteria.",
        "Automated passes and inapplicable rules do not establish Supports or Not Applicable. "
        "Pending review is a workflow state, not a VPAT conformance term.",
        "Only the selected WCAG version and levels are included. This does not assess "
        "additional Revised Section 508 or EN 301 549 requirements.",
        "Manual checks must cover full pages and complete processes, keyboard use and "
        "appropriate assistive technologies, not only the sampled controls.",
        "Screenshots may retain personal or confidential page content despite masking. "
        "Review evidence and reviewer notes before sharing this worksheet.",
        "Conformance selections are reviewer statements, not independently verified approvals.",
        *report.limitations,
    ]
    if report.unscanned or report.scan.status != "completed":
        limitations.append(
            "The source scan contains unscanned areas or did not complete. Missing coverage "
            "must be evaluated separately; it is not evidence of conformance."
        )
    if report.scan.wcag.startswith(("2.0-", "2.1-")):
        limitations.append(
            "WCAG 4.1.1 has special treatment under current errata and is removed in WCAG 2.2. "
            "Consult the selected standard and official template instructions."
        )
    now = datetime.now(UTC)
    return DraftACR(
        generated_at=now, report_date=now.date(), source_scan_id=report.scan.scan_id,
        source_sha256=report_digest(report), application_url=report.application.url,
        scan_status=report.scan.status, product=reviews.product,
        wcag_version=reviews.wcag_version, level=reviews.level, evaluation_methods=methods,
        coverage=report.coverage.model_dump(), limitations=limitations, criteria=rows,
        evidence=report.evidence, unmapped_finding_ids=unmapped,
    )


def compare_assessments(baseline: DraftACR, current: DraftACR) -> list[dict[str, str | None]]:
    """Show reviewer assessment changes, not an automated whole-product improvement score."""
    if baseline.product.application_id != current.product.application_id:
        raise ValueError("ACR application IDs differ")
    if (baseline.wcag_version, baseline.level) != (current.wcag_version, current.level):
        raise ValueError("ACR WCAG targets differ")
    if any(
        getattr(baseline.product, key) != getattr(current.product, key)
        for key in ("scope", "environment", "user_role")
    ):
        raise ValueError("ACR scope, environment or user role differs")
    before = {row.criterion_id: row for row in baseline.criteria}
    changes = []
    for row in current.criteria:
        old = before.get(row.criterion_id)
        old_value = old.review.conformance if old else None
        new_value = row.review.conformance
        unverified = (
            not old or bool(old.conflicts) or bool(row.conflicts)
            or old_value in {None, "Not Evaluated"} or new_value in {None, "Not Evaluated"}
        )
        changes.append({
            "criterion_id": row.criterion_id, "title": row.title,
            "baseline_conformance": old_value, "current_conformance": new_value,
            "status": "not_verified" if unverified else "unchanged"
            if old_value == new_value else "assessment_changed",
            "reason": "Missing, conflicting or unevaluated reviewer assessment" if unverified
            else "Reviewer assessments; a change is not an automatic resolution finding",
        })
    return changes
