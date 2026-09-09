"""Human-reviewed WCAG reporting contracts, separate from scanner findings."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from accessibility_agent.config.settings import Model
from accessibility_agent.models import Evidence

Conformance = Literal[
    "Supports", "Partially Supports", "Does Not Support", "Not Applicable", "Not Evaluated"
]


class Product(Model):
    application_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    organization: str = ""
    description: str = ""
    contact: str = ""
    environment: str = ""
    user_role: str = ""
    notes: str = ""

    @model_validator(mode="after")
    def meaningful_identity(self) -> "Product":
        if not all(value.strip() for value in (
            self.application_id, self.name, self.version, self.scope
        )):
            raise ValueError("Application identity, product version and scope cannot be blank")
        return self


class CriterionReview(Model):
    criterion_id: str
    conformance: Conformance | None = None
    reviewer: str = ""
    reviewed_on: date | None = None
    methods: str = ""
    remarks: str = ""
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_review_evidence(self) -> "CriterionReview":
        if self.conformance is not None:
            if not all(value.strip() for value in (self.reviewer, self.methods, self.remarks)):
                raise ValueError("A conformance decision needs reviewer, methods and remarks")
            if self.reviewed_on is None:
                raise ValueError("A conformance decision needs a review date")
            if not any(value.strip() for value in self.evidence):
                raise ValueError("A conformance decision needs an evidence reference")
        return self


class ReviewSet(Model):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["WCAG_REVIEW"] = "WCAG_REVIEW"
    source_scan_id: str
    source_sha256: str
    product: Product
    wcag_version: Literal["2.0", "2.1", "2.2"]
    level: Literal["A", "AA", "AAA"]
    criteria: list[CriterionReview]

    @model_validator(mode="after")
    def unique_criteria(self) -> "ReviewSet":
        ids = [review.criterion_id for review in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate criterion reviews")
        return self


class FindingReference(Model):
    finding_id: str
    rule_id: str
    title: str
    classification: str
    source: str
    occurrence_count: int
    urls: list[str]
    selectors: list[str]
    evidence_ids: list[str]


class CriterionAssessment(Model):
    criterion_id: str
    title: str
    level: str
    url: str
    automated_status: Literal["violations", "needs_review", "observations_only", "not_tested"]
    observations: dict[str, int]
    findings: list[FindingReference]
    checklist: list[str]
    review: CriterionReview
    conflicts: list[str] = Field(default_factory=list)


class DraftACR(Model):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["DRAFT_ACR"] = "DRAFT_ACR"
    generated_at: datetime
    report_date: date
    source_scan_id: str
    source_sha256: str
    application_url: str
    scan_status: str
    product: Product
    wcag_version: Literal["2.0", "2.1", "2.2"]
    level: Literal["A", "AA", "AAA"]
    edition: str = "WCAG preparation worksheet for ITI VPAT 2.5Rev WCAG edition"
    evaluation_methods: list[str]
    coverage: dict[str, int]
    limitations: list[str]
    criteria: list[CriterionAssessment]
    evidence: list[Evidence]
    unmapped_finding_ids: list[str]
    terms: dict[str, str] = Field(default_factory=lambda: {
        "Supports": "At least one method meets the criterion without known defects, "
        "or equivalent facilitation is provided.",
        "Partially Supports": "Some product functionality does not meet the criterion.",
        "Does Not Support": "Most product functionality does not meet the criterion.",
        "Not Applicable": "The criterion is not relevant to this product and scope.",
        "Not Evaluated": "The criterion has not been evaluated; permitted only for Level AAA.",
    })

    @property
    def pending_count(self) -> int:
        return sum(row.review.conformance is None for row in self.criteria)

    @property
    def conflict_count(self) -> int:
        return sum(bool(row.conflicts) for row in self.criteria)

    def review_set(self) -> ReviewSet:
        return ReviewSet(
            source_scan_id=self.source_scan_id, source_sha256=self.source_sha256,
            product=self.product, wcag_version=self.wcag_version, level=self.level,
            criteria=[row.review for row in self.criteria],
        )
