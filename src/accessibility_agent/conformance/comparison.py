"""Conservative, evidence-based comparison of two saved scan reports.

An absent finding is not a fix. Resolution requires the affected target in an
explicit passing rule result in comparable, complete coverage. Pairwise reports
cannot establish that an issue has returned after an earlier resolution.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from accessibility_agent.models import Finding, Occurrence, Report, RuleResult, ScanResult, State

_STATUSES = ("new", "still_present", "resolved", "not_verified", "newly_observed")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _url(value: str) -> str:
    """Normalize origin spelling without deleting query values or SPA fragments."""
    try:
        parts = urlsplit(value.strip())
        if not parts.hostname:
            return value.strip()
        hostname = parts.hostname.lower()
        if ":" in hostname:
            hostname = f"[{hostname}]"
        port = parts.port
        if port and (parts.scheme.lower(), port) not in {("https", 443), ("http", 80)}:
            hostname += f":{port}"
        if "@" in parts.netloc:
            hostname = parts.netloc.rsplit("@", 1)[0] + "@" + hostname
        return urlunsplit(
            (parts.scheme.lower(), hostname, parts.path or "/", parts.query, parts.fragment)
        )
    except ValueError:
        return value.strip()


def _target(node: Occurrence) -> str:
    # axe selectors are also serialized target arrays. Preserve shadow/frame
    # nesting, and accept the equivalent plain CSS selector representation.
    if node.target:
        return _json(node.target)
    selector = node.selector.strip()
    try:
        parsed = json.loads(selector)
        if isinstance(parsed, list) and parsed:
            return _json(parsed)
    except (ValueError, TypeError):
        pass
    return _json([selector]) if selector else ""


@dataclass(frozen=True)
class _Scope:
    url: str
    context: str
    verified: bool


@dataclass
class _Issue:
    finding: Finding
    occurrence: Occurrence
    scope: _Scope
    observations: set[tuple[str, str, str]] = field(default_factory=set)


class _Index:
    def __init__(self, report: Report) -> None:
        self.report = report
        self.states = {state.state_id: state for state in report.graph.states}
        self.results: dict[str, list[ScanResult]] = defaultdict(list)
        for result in report.results:
            self.results[result.state_id].append(result)
        self.scopes = {state.state_id: self._scope(state) for state in report.graph.states}
        self.scope_states: dict[_Scope, list[State]] = defaultdict(list)
        for state_id, state_scope in self.scopes.items():
            self.scope_states[state_scope].append(self.states[state_id])
        self.issues: dict[str, _Issue] = {}
        # Both stores can contain the same observations. Count each state/target
        # once, while retaining repeated visits recorded as distinct states.
        findings = [*report.findings, *(f for r in report.results for f in r.findings)]
        for finding in findings:
            for node in finding.occurrences:
                scope = self.scopes.get(node.state_id)
                if scope is None or scope.url != _url(node.url):
                    scope = _Scope(
                        _url(node.url),
                        _json(["unknown", report.scan.scan_id, node.state_id]),
                        False,
                    )
                target = _target(node)
                identity = _json(
                    [
                        finding.source,
                        finding.rule_id,
                        finding.classification,
                        scope.url,
                        scope.context,
                        target or ["unknown", report.scan.scan_id],
                    ]
                )
                issue = self.issues.setdefault(identity, _Issue(finding, node, scope))
                issue.observations.add((node.state_id, _url(node.url), target))

    def _scope(self, state: State) -> _Scope:
        actions = {action.action_id: action for action in self.report.graph.actions}
        replay: list[object] = []
        uncertain = False
        for action_id in state.replay_actions:
            action = actions.get(action_id)
            if action is None or action.outcome != "executed" or not action.replayable:
                uncertain = True
                break
            # Form input values are intentionally absent from the report. Such
            # replay paths cannot prove equivalent state after DOM drift.
            if action.action_type == "fill" or not action.element.selector:
                uncertain = True
            source = self.states.get(action.source_state)
            replay.append(
                [
                    action.action_type,
                    action.element.selector,
                    action.element.tag,
                    action.element.role,
                    action.element.accessible_name,
                    action.key,
                    action.option_index,
                    _url(source.url) if source else "",
                    action.element.attributes.get("href", ""),
                ]
            )
        markers = [state.dialogs, state.menus, state.tabs, state.forms]
        anchored = bool(replay or any(markers)) and not uncertain
        context: list[object] = [state.fingerprint_version, replay, markers]
        if not anchored:
            # URL and scan-specific state hashes alone do not establish UI
            # equivalence. An unchanged DOM fingerprint supplies the fallback.
            context.extend(["dom", state.dom_hash])
        verified = bool(anchored or state.dom_hash) and not uncertain
        if not verified:
            context.extend(["unknown", self.report.scan.scan_id, state.state_id])
        return _Scope(_url(state.url), _json(context), verified)


def _source_matches(result: ScanResult, issue: _Issue) -> bool:
    if issue.finding.source == "AXE":
        return result.engine.lower() in {"axe", "axe-core"}
    # Other result sources need explicit provenance; a similarly named rule in
    # an unrelated engine must never clear this source's finding.
    return (
        any(
            finding.source == issue.finding.source and finding.rule_id == issue.finding.rule_id
            for finding in result.findings
        )
        and issue.finding.source != "AI"
    )


def _runs(index: _Index, issue: _Issue) -> tuple[list[tuple[ScanResult, list[RuleResult]]], str]:
    if index.report.scan.status != "completed":
        return [], "The scan is failed, partial, or unfinished; coverage is not complete."
    if not issue.scope.verified:
        return [], "The affected UI state cannot be identified from saved evidence."
    states = index.scope_states.get(issue.scope, [])
    if not states:
        return [], "The affected URL and UI state were not comparably covered; state or DOM drift."
    runs: list[tuple[ScanResult, list[RuleResult]]] = []
    for state in states:
        if state.status != "scanned":
            return [], "An affected UI state was failed, partial, skipped, or not scanned."
        if any(
            area.state_id == state.state_id
            or (area.state_id is None and _url(area.url) == issue.scope.url)
            for area in index.report.unscanned
        ):
            return [], "The report records omitted or unscanned coverage for the affected state."
        results = [r for r in index.results.get(state.state_id, []) if _source_matches(r, issue)]
        if not results:
            return [], "No result with verified source/engine provenance covers the affected state."
        for result in results:
            rules = [r for r in result.rules if r.rule_id == issue.finding.rule_id]
            if not rules:
                return (
                    [],
                    "The affected rule was not explicitly retested in the comparable UI state.",
                )
            if not result.engine_version:
                return [], "The engine version is missing."
            runs.append((result, rules))
    return runs, ""


def _matches_node(node: Occurrence, result: ScanResult, issue: _Issue) -> bool:
    return (
        bool(_target(issue.occurrence))
        and node.state_id == result.state_id
        and _url(node.url) == issue.scope.url
        and _target(node) == _target(issue.occurrence)
    )


def _absence(
    issue: _Issue, observed: _Index, absent: _Index, *, resolving: bool
) -> tuple[bool, str]:
    if (
        observed.report.scan.wcag != absent.report.scan.wcag
        or observed.report.configuration != absent.report.configuration
    ):
        return False, "WCAG scope or recorded scan settings differ; retest is not comparable."
    original, reason = _runs(observed, issue)
    if reason:
        return False, reason
    retested, reason = _runs(absent, issue)
    if reason:
        return False, reason
    original_engines = {(result.engine, result.engine_version) for result, _ in original}
    retest_engines = {(result.engine, result.engine_version) for result, _ in retested}
    if original_engines != retest_engines:
        return False, "The affected rule's engine or version changed; retest is not comparable."
    if issue.finding.classification == "AI_INFERENCE":
        return False, "AI inferences do not establish deterministic release changes."
    original_outcome = "violation" if issue.finding.classification == "VIOLATION" else "incomplete"
    if not any(
        rule.outcome == original_outcome
        and any(_matches_node(n, result, issue) for n in rule.nodes)
        for result, rules in original
        for rule in rules
    ):
        return False, "The finding lacks matching rule/target evidence in its original scan."
    if any(
        other.scope == issue.scope
        and other.finding.source == issue.finding.source
        and other.finding.rule_id == issue.finding.rule_id
        and _target(other.occurrence) == _target(issue.occurrence)
        for other in absent.issues.values()
    ):
        return False, "The same target still has a recorded issue or review finding for this rule."
    for result, rules in retested:
        if any(rule.outcome == "incomplete" for rule in rules):
            return False, "The affected rule has incomplete evidence in the comparable UI state."
        if any(
            rule.outcome == "violation"
            and (not rule.nodes or any(_matches_node(n, result, issue) for n in rule.nodes))
            for rule in rules
        ):
            return False, "Rule evidence still violates or cannot exclude the affected target."
        if resolving and not any(
            rule.outcome == "pass" and any(_matches_node(n, result, issue) for n in rule.nodes)
            for rule in rules
        ):
            return False, (
                "The old target has no explicit passing evidence; absence, selector changes, "
                "and inapplicable rules alone do not verify a fix."
            )
    if resolving:
        return (
            True,
            "The same target explicitly passed the rule in comparable, complete UI coverage.",
        )
    return (
        True,
        "The issue was absent where the same rule was explicitly tested in baseline coverage.",
    )


def compare_reports(baseline: Report, current: Report) -> dict[str, Any]:
    """Return JSON-serializable issue changes without mutating either report.

    ``new`` requires comparable baseline rule coverage; ``newly_observed`` means
    that coverage is not established. ``still_present`` describes the same
    recorded issue in both reports, even when changed settings limit comparison.
    Occurrence counts deduplicate repeated storage of a state/target observation.
    """
    before, after = _Index(baseline), _Index(current)
    warnings = [
        "Pairwise comparison cannot determine returned issues; multi-scan history is required.",
        "Resolution is limited to explicit passing evidence for the exact target and UI state; "
        "this comparison is not a conformance determination.",
    ]
    if baseline.configuration != current.configuration or baseline.scan.wcag != current.scan.wcag:
        warnings.append(
            "Recorded settings or WCAG scope differ; absent findings cannot prove fixes."
        )
    if any(report.scan.status != "completed" for report in (baseline, current)):
        warnings.append(
            "At least one scan has incomplete coverage; absent findings are unverified."
        )
    engines_before = {(r.engine, r.engine_version) for r in baseline.results}
    engines_after = {(r.engine, r.engine_version) for r in current.results}
    if engines_before != engines_after:
        warnings.append("Engine/version coverage differs between reports.")
    items: list[dict[str, Any]] = []
    counts = dict.fromkeys(_STATUSES, 0)
    for identity in sorted(before.issues.keys() | after.issues.keys()):
        old, new = before.issues.get(identity), after.issues.get(identity)
        issue = new or old
        assert issue is not None
        if old is not None and new is not None:
            status = "still_present"
            reason = (
                "The same source, classification, rule, URL, UI context, and target occur in both."
            )
        elif old is not None:
            verified, reason = _absence(old, before, after, resolving=True)
            status = "resolved" if verified else "not_verified"
        else:
            verified, reason = _absence(issue, after, before, resolving=False)
            status = "new" if verified else "newly_observed"
        counts[status] += 1
        items.append(
            {
                "key": hashlib.sha256(identity.encode()).hexdigest(),
                "status": status,
                "rule_id": issue.finding.rule_id,
                "title": issue.finding.title,
                "source": issue.finding.source,
                "classification": issue.finding.classification,
                "url": issue.scope.url,
                "selector": issue.occurrence.selector,
                "target": json.loads(_target(issue.occurrence))
                if _target(issue.occurrence)
                else [],
                "baseline_occurrences": len(old.observations) if old else 0,
                "current_occurrences": len(new.observations) if new else 0,
                "baseline_state_ids": sorted({n[0] for n in old.observations}) if old else [],
                "current_state_ids": sorted({n[0] for n in new.observations}) if new else [],
                "reason": reason,
            }
        )
    if counts["newly_observed"]:
        warnings.append(
            "Newly observed issues may reflect new coverage, changed settings, or uncertain UI "
            "state identity; they are not established regressions."
        )
    return {
        "baseline_scan_id": baseline.scan.scan_id,
        "current_scan_id": current.scan.scan_id,
        "counts": counts,
        "items": items,
        "warnings": warnings,
    }
