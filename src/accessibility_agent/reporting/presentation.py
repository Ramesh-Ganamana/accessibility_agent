"""Compact HTML views; raw scanner observations remain in the JSON report."""

import hashlib
import secrets
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from accessibility_agent.findings.repository import MemoryFindingRepository
from accessibility_agent.models import Evidence, Finding, Impact, Occurrence, Report


@dataclass
class Screenshot:
    anchor: str
    path: str
    description: str
    states: dict[str, str] = field(default_factory=dict)


@dataclass
class ScreenshotReference:
    screenshot: Screenshot
    marker: int | None
    note: str
    states: list[str] = field(default_factory=list)


@dataclass
class AffectedElement:
    occurrence: Occurrence
    states: list[str] = field(default_factory=list)
    screenshots: list[ScreenshotReference] = field(default_factory=list)
    other_evidence: dict[str, Evidence] = field(default_factory=dict)


@dataclass
class IssueGroup:
    finding: Finding
    elements: list[AffectedElement]
    occurrence_count: int
    url_count: int
    state_count: int


@dataclass
class CrawlRow:
    url: str
    status: str = "discovered"
    states: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass
class GraphNode:
    state_id: str
    title: str
    url: str
    status: str
    x: int
    y: int


@dataclass
class GraphEdge:
    label: str
    description: str
    path: str
    x: int
    y: int


@dataclass
class ReportPresentation:
    issues: list[IssueGroup]
    screenshots: list[Screenshot]
    severity_counts: dict[Impact, int]
    crawl_rows: list[CrawlRow]
    url_status_counts: dict[str, int]
    state_status_counts: dict[str, int]
    interaction_counts: dict[str, int]
    duration: str
    evidence: list[Evidence]
    state_screenshots: dict[str, str]
    graph_nodes: list[GraphNode]
    graph_edges: list[GraphEdge]
    graph_height: int
    graph_omitted_states: int
    graph_omitted_actions: int
    script_nonce: str = field(default_factory=lambda: secrets.token_urlsafe(24))


def _local_evidence_path(output_dir: Path, value: str) -> Path | None:
    value = unquote(value)
    path = Path(value)
    if path.is_absolute() or ":" in value or value.startswith(("/", "\\")):
        return None
    candidate = (output_dir / path).resolve()
    return candidate if candidate.is_relative_to(output_dir.resolve()) else None


def _crawl_rows(report: Report) -> list[CrawlRow]:
    """A URL's strongest observed status wins; separate skipped actions stay in reasons."""
    rows: dict[str, CrawlRow] = {}
    actions = {action.action_id: action for action in report.graph.actions}
    rank = {"discovered": 0, "skipped": 1, "failed": 2, "partial": 3, "scanned": 4}
    for state in report.graph.states:
        row = rows.setdefault(state.url, CrawlRow(url=state.url))
        row.states.append(state.state_id)
        if rank[state.status] > rank[row.status]:
            row.status = state.status
    for area in report.unscanned:
        row = rows.setdefault(area.url, CrawlRow(url=area.url))
        reason = area.reason + (f": {area.detail}" if area.detail else "")
        if reason not in row.reasons:
            row.reasons.append(reason)
        # Action/background-request skips do not imply that the URL itself was skipped.
        if not row.states:
            action = actions.get(area.action_id or "")
            failed = (
                bool(action and action.outcome == "failed")
                or area.reason == "page_readiness_timeout"
                or (area.reason.startswith("navigation_") and area.reason.endswith("Error"))
                or (area.reason.startswith("http_") and area.reason[5:].isdigit()
                    and int(area.reason[5:]) >= 400)
            )
            status = "failed" if failed else "skipped"
            if rank[status] > rank[row.status]:
                row.status = status
    return sorted(rows.values(), key=lambda row: row.url)


def _graph_view(report: Report) -> tuple[list[GraphNode], list[GraphEdge]]:
    nodes = [
        GraphNode(
            state.state_id, state.title, state.url, state.status,
            40 + (index % 3) * 360, 50 + (index // 3) * 170,
        )
        for index, state in enumerate(report.graph.states[:36])
    ]
    by_id = {node.state_id: node for node in nodes}
    edges = []
    for action in report.graph.actions:
        source, target = by_id.get(action.source_state), by_id.get(action.target_state or "")
        if source is None or target is None:
            continue
        x1, y1 = source.x + 150, source.y + 90
        x2, y2 = target.x + 150, target.y
        if source is target:
            path = f"M {source.x + 300} {source.y + 45} c 60 -70 60 100 0 35"
            label_x, label_y = source.x + 285, source.y + 113
        else:
            middle = (y1 + y2) // 2
            path = f"M {x1} {y1} C {x1} {middle}, {x2} {middle}, {x2} {y2}"
            label_x, label_y = (x1 + x2) // 2, middle - 7
        label = f"{action.action_type}: {action.element.accessible_name or action.element.tag}"
        edges.append(GraphEdge(
            label, f"{action.source_state} → {action.target_state}: {label} ({action.outcome})",
            path, label_x, label_y,
        ))
        if len(edges) == 72:
            break
    return nodes, edges


def _image_identity(path: Path) -> str:
    try:
        with path.open("rb") as image:
            return "sha256:" + hashlib.file_digest(image, "sha256").hexdigest()
    except OSError:
        # Missing evidence is still linked for diagnosis, but is never equated with another file.
        return "path:" + str(path)


def _issue_findings(report: Report) -> list[Finding]:
    """Impact changes do not create another copy of the same user-facing issue."""
    repository = MemoryFindingRepository()
    repository.add(report.findings)
    grouped: dict[tuple[str, str, str], Finding] = {}
    impact_rank = {
        None: 0,
        Impact.MINOR: 1,
        Impact.MODERATE: 2,
        Impact.SERIOUS: 3,
        Impact.CRITICAL: 4,
    }
    for finding in repository.all():
        key = (finding.source, finding.rule_id, finding.classification)
        if key not in grouped:
            combined = finding.model_copy(deep=True)
            combined.id = f"A11Y-{len(grouped) + 1:04d}"
            grouped[key] = combined
            continue
        combined = grouped[key]
        combined.occurrences.extend(item.model_copy(deep=True) for item in finding.occurrences)
        if impact_rank[finding.rule_impact] > impact_rank[combined.rule_impact]:
            combined.rule_impact = finding.rule_impact
        combined.wcag = list(dict.fromkeys([*combined.wcag, *finding.wcag]))
        combined.principles = list(dict.fromkeys([*combined.principles, *finding.principles]))
    return list(grouped.values())


def _example_quality(
    occurrence: Occurrence,
    screenshots_by_id: dict[str, Screenshot],
    evidence_by_id: dict[str, Evidence],
) -> tuple[bool, bool, bool]:
    has_screenshot = any(item in screenshots_by_id for item in occurrence.evidence)
    return (
        has_screenshot and occurrence.screenshot_marker is not None,
        has_screenshot,
        any(item in evidence_by_id for item in occurrence.evidence),
    )


def present_report(report: Report, output_dir: Path) -> ReportPresentation:
    findings = _issue_findings(report)
    safe_evidence = [
        item for item in report.evidence if _local_evidence_path(output_dir, item.path) is not None
    ]
    evidence_by_id = {item.evidence_id: item for item in safe_evidence}
    urls = {item.state_id: item.url for finding in findings for item in finding.occurrences}
    urls.update({state.state_id: state.url for state in report.graph.states})
    screenshots: dict[str, Screenshot] = {}
    screenshots_by_id: dict[str, Screenshot] = {}
    image_paths: dict[Path, str] = {}
    for evidence in report.evidence:
        if evidence.kind != "screenshot":
            continue
        path = _local_evidence_path(output_dir, evidence.path)
        if path is None:
            continue
        if path not in image_paths:
            image_paths[path] = _image_identity(path)
        identity = image_paths[path]
        if identity not in screenshots:
            screenshots[identity] = Screenshot(
                anchor=f"screenshot-{len(screenshots) + 1}",
                path=evidence.path,
                description=evidence.description,
            )
        screenshot = screenshots[identity]
        screenshot.states[evidence.state_id] = urls.get(evidence.state_id, "")
        screenshots_by_id[evidence.evidence_id] = screenshot

    issues = []
    used_screenshots: set[str] = set()
    severity_counts = dict.fromkeys(Impact, 0)
    for finding in findings:
        element = AffectedElement(occurrence=finding.occurrences[0])
        for occurrence in finding.occurrences:
            # Count every observation, but show this issue's strongest example only once.
            if occurrence.state_id not in element.states:
                element.states.append(occurrence.state_id)
            if _example_quality(occurrence, screenshots_by_id, evidence_by_id) > _example_quality(
                element.occurrence, screenshots_by_id, evidence_by_id
            ):
                element.occurrence = occurrence

        # Evidence and marker numbers belong to the selected observation only.
        occurrence = element.occurrence
        for evidence_id in occurrence.evidence:
            linked_screenshot = screenshots_by_id.get(evidence_id)
            if linked_screenshot is not None:
                if not element.screenshots:
                    linked_screenshot.states[occurrence.state_id] = occurrence.url
                    used_screenshots.add(linked_screenshot.anchor)
                    element.screenshots.append(
                        ScreenshotReference(
                            linked_screenshot,
                            occurrence.screenshot_marker,
                            occurrence.screenshot_note,
                            states=[occurrence.state_id],
                        )
                    )
            elif (linked_evidence := evidence_by_id.get(evidence_id)) is not None:
                if linked_evidence.kind != "screenshot":
                    element.other_evidence.setdefault(linked_evidence.path, linked_evidence)
        issues.append(
            IssueGroup(
                finding=finding,
                elements=[element],
                occurrence_count=len(finding.occurrences),
                url_count=len({item.url for item in finding.occurrences}),
                state_count=len({item.state_id for item in finding.occurrences}),
            )
        )
        if finding.classification == "VIOLATION" and finding.rule_impact is not None:
            severity_counts[finding.rule_impact] += 1

    # Keep page context for pages without finding examples, once per URL.
    pages_with_examples = {item.url for finding in findings for item in finding.occurrences}
    context_pages: set[str] = set()
    for screenshot in screenshots.values():
        for state_id, url in screenshot.states.items():
            page = url or state_id
            if page not in pages_with_examples and page not in context_pages:
                used_screenshots.add(screenshot.anchor)
                context_pages.add(page)
    crawl_rows = _crawl_rows(report)
    nodes, edges = _graph_view(report)
    duration = "In progress" if report.scan.status == "running" else "Not recorded"
    if report.scan.completed_at:
        seconds = max(0, int((report.scan.completed_at - report.scan.started_at).total_seconds()))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        duration = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
    return ReportPresentation(
        issues=issues,
        screenshots=[shot for shot in screenshots.values() if shot.anchor in used_screenshots],
        severity_counts=severity_counts,
        crawl_rows=crawl_rows,
        url_status_counts=dict(Counter(row.status for row in crawl_rows)),
        state_status_counts=dict(Counter(state.status for state in report.graph.states)),
        interaction_counts=dict(Counter(
            action.outcome for action in report.graph.actions if action.action_type != "navigate"
        )),
        duration=duration,
        evidence=safe_evidence,
        state_screenshots={
            state.state_id: state.screenshot for state in report.graph.states
            if state.screenshot and _local_evidence_path(output_dir, state.screenshot) is not None
        },
        graph_nodes=nodes,
        graph_edges=edges,
        graph_height=max(210, ((len(nodes) + 2) // 3) * 170 + 40),
        graph_omitted_states=max(0, len(report.graph.states) - len(nodes)),
        graph_omitted_actions=len(report.graph.actions) - len(edges),
    )
