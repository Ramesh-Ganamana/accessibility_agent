"""Local checksum-verified axe-core with deterministic result conversion."""

import asyncio
import hashlib
import json
import re
from importlib.resources import files
from typing import Any, Literal

from playwright.async_api import Error

from accessibility_agent.config.settings import Accessibility
from accessibility_agent.crawler.discovery import readable_frame
from accessibility_agent.interfaces import BrowserSession
from accessibility_agent.models import Finding, Occurrence, RuleResult, ScanResult, State
from accessibility_agent.utils.privacy import Redactor


def wcag_criteria(tags: list[str]) -> list[str]:
    return sorted(
        {
            f"{m[1]}.{m[2]}.{m[3]}"
            for tag in tags
            if (m := re.fullmatch(r"wcag([1-4])([1-5])(\d{1,2})", tag))
        }
    )


def rule_tags(settings: Accessibility) -> list[str]:
    levels = ["a", "aa", "aaa"][: ["A", "AA", "AAA"].index(settings.level) + 1]
    versions = ["2", "21"] + (["22"] if settings.version == "2.2" else [])
    return [f"wcag{version}{level}" for version in versions for level in levels]


def load_bundle() -> str:
    root = files("accessibility_agent.accessibility").joinpath("vendor")
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    bundle = root.joinpath("axe.min.js").read_bytes()
    if hashlib.sha256(bundle).hexdigest() != manifest["sha256"]:
        raise RuntimeError("axe_bundle_checksum_mismatch")
    return bundle.decode("utf-8")


def convert(raw: dict[str, Any], state: State, redactor: Redactor) -> ScanResult:
    result = ScanResult(
        state_id=state.state_id, engine="axe-core", engine_version=str(raw["testEngine"]["version"])
    )
    outcomes: dict[str, Literal["violation", "incomplete", "pass", "inapplicable"]] = {
        "violations": "violation",
        "incomplete": "incomplete",
        "passes": "pass",
        "inapplicable": "inapplicable",
    }
    for group, outcome in outcomes.items():
        for rule in raw.get(group, []):
            occurrences = []
            for node in rule.get("nodes", []):
                target = node.get("target", [])
                occurrences.append(
                    Occurrence(
                        url=state.url,
                        state_id=state.state_id,
                        element="axe-node",
                        selector=redactor.text(json.dumps(target)),
                        target=json.loads(redactor.text(json.dumps(target))),
                        html=redactor.html(node.get("html", "")),
                        failure_summary=redactor.text(node.get("failureSummary", "")),
                    )
                )
            rule_result = RuleResult(
                rule_id=rule["id"],
                outcome=outcome,
                description=rule["description"],
                impact=rule.get("impact"),
                help=rule["help"],
                help_url=rule["helpUrl"],
                tags=rule["tags"],
                nodes=occurrences,
            )
            result.rules.append(rule_result)
            if outcome not in {"violation", "incomplete"} or not occurrences:
                continue
            criteria = wcag_criteria(rule["tags"])
            principles: list[Literal["Perceivable", "Operable", "Understandable", "Robust"]] = []
            names: dict[str, Literal["Perceivable", "Operable", "Understandable", "Robust"]] = {
                "1": "Perceivable",
                "2": "Operable",
                "3": "Understandable",
                "4": "Robust",
            }
            for criterion in criteria:
                if names[criterion[0]] not in principles:
                    principles.append(names[criterion[0]])
            result.findings.append(
                Finding(
                    id=f"{state.state_id[:12]}-{rule['id']}-{outcome}",
                    title=rule["help"],
                    rule_id=rule["id"],
                    rule_impact=rule.get("impact"),
                    source="AXE",
                    classification="VIOLATION" if outcome == "violation" else "NEEDS_REVIEW",
                    wcag=criteria,
                    principles=principles,
                    tags=rule["tags"],
                    description=rule["description"],
                    help=rule["help"],
                    help_url=rule["helpUrl"],
                    recommendation=(
                        "Review the node failure summary and linked axe remediation guidance."
                    ),
                    occurrences=occurrences,
                )
            )
    return result


class AxeScanner:
    def __init__(self, settings: Accessibility, redactor: Redactor, timeout_ms: int) -> None:
        self.tags, self.redactor = rule_tags(settings), redactor
        self.timeout_ms = timeout_ms
        self.bundle = load_bundle()

    async def scan(self, session: BrowserSession, state: State) -> ScanResult:
        async with asyncio.timeout(self.timeout_ms / 1000):
            # Evaluate directly so a restrictive page CSP does not require a remote script tag.
            await session.page.evaluate(self.bundle)
            for frame in session.page.frames:
                if frame == session.page.main_frame or not await readable_frame(frame):
                    continue
                try:
                    await frame.evaluate(self.bundle)
                except Error:
                    # A frame can navigate/detach between inventory and injection.
                    # axe's frame-tested check reports unresponsive frames for review.
                    continue
            raw = await session.page.evaluate(
                """tags => axe.run(document, {
                    runOnly:{type:'tag',values:tags}, iframes:true,
                    rules:{'frame-tested':{enabled:true}}
                })""",
                self.tags,
            )
        return convert(raw, state, self.redactor)
