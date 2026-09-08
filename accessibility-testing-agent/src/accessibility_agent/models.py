"""Serializable domain contracts; no scanning behavior is implemented here."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from accessibility_agent.config.settings import Model


class Safety(StrEnum):
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    DESTRUCTIVE = "DESTRUCTIVE"
    EXTERNAL = "EXTERNAL"
    UNKNOWN = "UNKNOWN"


class Impact(StrEnum):
    CRITICAL = "critical"
    SERIOUS = "serious"
    MODERATE = "moderate"
    MINOR = "minor"


class Element(Model):
    element_id: str
    selector: str
    tag: str
    role: str = ""
    accessible_name: str = ""
    html: str = ""
    visible: bool = True
    attributes: dict[str, str] = Field(default_factory=dict)


class State(Model):
    state_id: str
    url: str
    title: str
    dom_hash: str
    accessibility_tree_hash: str | None = None
    fingerprint_version: str = "1"
    depth: int = Field(default=0, ge=0)
    screenshot: str | None = None
    replay_actions: list[str] = Field(default_factory=list)
    visible_elements: list[Element] = Field(default_factory=list)
    discovered_links: list[Element] = Field(default_factory=list)
    interactive_elements: list[Element] = Field(default_factory=list)
    dialogs: list[str] = Field(default_factory=list)
    menus: list[str] = Field(default_factory=list)
    tabs: list[str] = Field(default_factory=list)
    forms: list[str] = Field(default_factory=list)
    status: Literal["discovered", "scanned", "partial", "failed", "skipped"] = "discovered"


class Action(Model):
    action_id: str
    source_state: str
    action_type: Literal["navigate", "click", "keypress", "fill", "select"]
    element: Element
    target_state: str | None = None
    safety: Safety = Safety.UNKNOWN
    key: str | None = None
    outcome: Literal["pending", "executed", "skipped", "failed"] = "pending"
    reason: str | None = None
    option_index: int | None = Field(default=None, ge=0)
    replayable: bool = True
    replay_count: int = Field(default=0, ge=0)


class Graph(Model):
    states: list[State] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> Graph:
        ids = {state.state_id for state in self.states}
        if len(ids) != len(self.states):
            raise ValueError("Duplicate state IDs")
        if len({action.action_id for action in self.actions}) != len(self.actions):
            raise ValueError("Duplicate action IDs")
        for action in self.actions:
            if action.source_state not in ids:
                raise ValueError("Unknown source state")
            if action.target_state is not None and action.target_state not in ids:
                raise ValueError("Unknown target state")
        return self


class Evidence(Model):
    evidence_id: str
    state_id: str
    kind: Literal["screenshot", "dom", "accessibility_tree", "keyboard"]
    path: str
    description: str = ""


class Occurrence(Model):
    url: str
    state_id: str
    element: str
    selector: str
    target: list[str | list[str]] = Field(default_factory=list)
    html: str = ""
    evidence: list[str] = Field(default_factory=list)
    screenshot_marker: int | None = Field(default=None, ge=1)
    screenshot_note: str = ""
    failure_summary: str = ""


class AIRecommendation(Model):
    classification: Literal["AI_INFERENCE"] = "AI_INFERENCE"
    confidence: float = Field(ge=0, le=1)
    explanation: str
    user_impact: str = ""
    recommendation: str = ""
    suggested_fix: str = ""
    suggested_business_severity: Impact | None = None


class Finding(Model):
    id: str
    title: str
    rule_id: str
    rule_impact: Impact | None = None
    business_severity: Impact | None = None
    source: Literal["AXE", "DOM", "KEYBOARD", "ARIA", "VISUAL", "AI"]
    classification: Literal["VIOLATION", "NEEDS_REVIEW", "AI_INFERENCE"]
    confidence: float = Field(default=1, ge=0, le=1)
    wcag: list[str] = Field(default_factory=list)
    principles: list[Literal["Perceivable", "Operable", "Understandable", "Robust"]] = Field(
        default_factory=list
    )
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    help: str = ""
    help_url: str = ""
    user_impact: str = ""
    recommendation: str = ""
    suggested_fix: str = ""
    occurrences: list[Occurrence] = Field(min_length=1)
    ai_recommendations: list[AIRecommendation] = Field(default_factory=list)

    @model_validator(mode="after")
    def ai_is_inference(self) -> Finding:
        if self.source == "AI" and self.classification != "AI_INFERENCE":
            raise ValueError("AI findings must be marked AI_INFERENCE")
        return self


class RuleResult(Model):
    rule_id: str
    outcome: Literal["violation", "incomplete", "pass", "inapplicable"]
    description: str = ""
    impact: Impact | None = None
    help: str = ""
    help_url: str = ""
    tags: list[str] = Field(default_factory=list)
    nodes: list[Occurrence] = Field(default_factory=list)


class ScanResult(Model):
    state_id: str
    engine: str
    engine_version: str
    rules: list[RuleResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)


class KeyboardObservation(Model):
    state_id: str
    element: Element
    key: Literal[
        "Tab",
        "Shift+Tab",
        "Enter",
        "Space",
        "Escape",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "Home",
        "End",
    ]
    focus_order: int = Field(ge=0)
    visible: bool
    focus_visible: bool | None = None
    reachable: bool | None = None
    notes: str = ""


class Coverage(Model):
    urls_discovered: int = Field(default=0, ge=0)
    urls_scanned: int = Field(default=0, ge=0)
    states_discovered: int = Field(default=0, ge=0)
    states_scanned: int = Field(default=0, ge=0)
    interactive_elements_discovered: int = Field(default=0, ge=0)
    interactive_elements_tested: int = Field(default=0, ge=0)
    keyboard_elements_tested: int = Field(default=0, ge=0)
    accessibility_checks_executed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> Coverage:
        for discovered, tested in (
            (self.urls_discovered, self.urls_scanned),
            (self.states_discovered, self.states_scanned),
            (self.interactive_elements_discovered, self.interactive_elements_tested),
        ):
            if tested > discovered:
                raise ValueError("Tested count exceeds discovered count")
        return self

    def percentages(self) -> dict[str, float | None]:
        return {
            name: round(tested / discovered * 100, 2) if discovered else None
            for name, discovered, tested in (
                ("urls", self.urls_discovered, self.urls_scanned),
                ("states", self.states_discovered, self.states_scanned),
                (
                    "interactive_elements",
                    self.interactive_elements_discovered,
                    self.interactive_elements_tested,
                ),
            )
        }


class UnscannedArea(Model):
    url: str
    state_id: str | None = None
    action_id: str | None = None
    reason: str
    detail: str = ""
    retryable: bool = False


class AuthenticationResult(Model):
    status: Literal["authenticated", "not_required", "blocked", "failed"]
    reason: str = ""


class ScanMetadata(Model):
    scan_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: Literal["running", "completed", "partial", "failed"]
    wcag: str = "2.2-AA"


class ApplicationInfo(Model):
    url: str
    title: str = ""


class Summary(Model):
    severity_counts: dict[Impact, int] = Field(default_factory=dict)
    health_score: float | None = Field(default=None, ge=0, le=100)
    disclaimer: str = "Internal metric only; not WCAG certification or whole-site coverage."


class Report(Model):
    schema_version: Literal["1.0"] = "1.0"
    application: ApplicationInfo
    scan: ScanMetadata
    configuration: dict[str, str | int | bool | None] = Field(default_factory=dict)
    coverage: Coverage = Field(default_factory=Coverage)
    summary: Summary = Field(default_factory=Summary)
    findings: list[Finding] = Field(default_factory=list)
    results: list[ScanResult] = Field(default_factory=list)
    keyboard: list[KeyboardObservation] = Field(default_factory=list)
    graph: Graph = Field(default_factory=Graph)
    evidence: list[Evidence] = Field(default_factory=list)
    unscanned: list[UnscannedArea] = Field(default_factory=list)
    authentication: AuthenticationResult | None = None
    limitations: list[str] = Field(default_factory=list)
