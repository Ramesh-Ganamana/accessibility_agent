"""State screenshots with input/private-region masking and sanitized DOM snippets."""

import json
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from playwright.async_api import Error

from accessibility_agent.interfaces import BrowserSession
from accessibility_agent.models import Evidence, Finding, Occurrence, State
from accessibility_agent.utils.privacy import Redactor


class StateEvidenceCollector:
    def __init__(self, output_dir: Path, redactor: Redactor, visual: bool) -> None:
        self.output_dir, self.redactor, self.visual = output_dir, redactor, visual

    async def capture(
        self, session: BrowserSession, state: State, findings: Sequence[Finding] = ()
    ) -> list[Evidence]:
        folder = self.output_dir / "evidence"
        folder.mkdir(parents=True, exist_ok=True)
        dom_path = f"evidence/{state.state_id}.json"
        # An inventory, not a full document dump: excludes inline scripts, storage and form values.
        (self.output_dir / dom_path).write_text(
            json.dumps(
                {
                    "state_id": state.state_id,
                    "elements": [e.model_dump(mode="json") for e in state.visible_elements],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        result = [
            Evidence(
                evidence_id=f"{state.state_id}-dom",
                state_id=state.state_id,
                kind="dom",
                path=dom_path,
                description="Sanitized element inventory",
            )
        ]
        if self.visual:
            (self.output_dir / "screenshots").mkdir(parents=True, exist_ok=True)
            shot = f"screenshots/{state.state_id}.png"
            masks = [
                session.page.locator(
                    "input,textarea,select,[contenteditable],[data-private],[data-sensitive]"
                )
            ]
            masks.extend(
                session.page.get_by_text(secret, exact=False) for secret in self.redactor.secrets
            )
            overlay_id = "a11y-evidence-" + uuid4().hex
            targets, occurrences = highlight_targets(findings, state.state_id, self.redactor)
            highlighted = []
            try:
                if targets:
                    highlighted = await session.page.evaluate(
                        HIGHLIGHT_OVERLAY, {"id": overlay_id, "targets": targets}
                    )
                await session.page.screenshot(
                    path=str(self.output_dir / shot),
                    full_page=True,
                    mask=masks,
                    animations="disabled",
                    caret="initial",  # Inputs are masked; avoid caret style mutations.
                    timeout=10000,
                )
            finally:
                # Keep evidence decoration out of later discovery, replay and axe scans.
                with suppress(Error):
                    await session.page.evaluate(
                        "id => document.getElementById(id)?.remove()", overlay_id
                    )
            for outcome, group in zip(highlighted, occurrences, strict=True):
                for occurrence in group:
                    occurrence.screenshot_marker = outcome["marker"]
                    occurrence.screenshot_note = outcome["note"]
            count = len({item["marker"] for item in highlighted if item["marker"] is not None})
            state.screenshot = shot
            result.append(
                Evidence(
                    evidence_id=f"{state.state_id}-screenshot",
                    state_id=state.state_id,
                    kind="screenshot",
                    path=shot,
                    description=(
                        f"Screenshot with {count} numbered, named issue highlights "
                        "(one example per issue; private data masked)"
                        if count
                        else "Masked state screenshot (no visible issue targets highlighted)"
                    ),
                )
            )
        return result


def highlight_targets(
    findings: Sequence[Finding], state_id: str, redactor: Redactor
) -> tuple[list[dict[str, object]], list[list[Occurrence]]]:
    """Keep candidates for each rule so the overlay can choose its first visible target."""
    targets: list[dict[str, object]] = []
    occurrences: list[list[Occurrence]] = []
    indices: dict[str, int] = {}
    for finding in findings:
        issue = json.dumps((finding.source, finding.rule_id, finding.classification))
        label = redactor.text(finding.title.strip() or finding.rule_id)
        if finding.classification == "NEEDS_REVIEW":
            label = "Needs review: " + label
        elif finding.classification == "AI_INFERENCE":
            label = "AI inference: " + label
        for occurrence in finding.occurrences:
            if occurrence.state_id != state_id:
                continue
            key = json.dumps((issue, occurrence.target))
            if key in indices:
                index = indices[key]
                occurrences[index].append(occurrence)
                continue
            indices[key] = len(targets)
            targets.append(
                {
                    "target": occurrence.target,
                    "issue": issue,
                    "label": label,
                    "violation": finding.classification == "VIOLATION",
                }
            )
            occurrences.append([occurrence])
    return targets, occurrences


# Draw temporary boxes and sanitized rule titles, never page text or input values.
# A closed shadow root isolates the marker style from the application's CSS.
HIGHLIGHT_OVERLAY = r"""({id, targets}) => {
    const root = document.createElement('div');
    root.id = id;
    root.setAttribute('aria-hidden', 'true');
    root.setAttribute('popover', 'manual');
    root.setAttribute('data-a11y-evidence-overlay', '');
    root.style.cssText = 'all:initial!important;position:absolute!important;' +
        'left:0!important;top:0!important;width:0!important;height:0!important;' +
        'pointer-events:none!important;z-index:2147483647!important';
    const shadow = root.attachShadow({mode:'closed'});
    const style = document.createElement('style');
    style.textContent = `
        .box {position:absolute;box-sizing:border-box;border:3px solid var(--ink);
            box-shadow:0 0 0 2px white,inset 0 0 0 1px white;border-radius:3px;
            background:transparent;pointer-events:none}
        .label {position:absolute;box-sizing:border-box;background:var(--ink);color:white;
            border:2px solid white;border-radius:5px;min-width:36px;
            padding:5px 8px;font:700 14px/19px Arial,sans-serif;text-align:left;
            box-shadow:0 1px 4px #0008;white-space:normal;overflow-wrap:anywhere;
            pointer-events:none}
        .issue + .issue {margin-top:4px;border-top:1px solid #ffffff80;padding-top:4px}
    `;
    shadow.append(style);
    const width = Math.max(document.documentElement.scrollWidth, innerWidth);
    const height = Math.max(document.documentElement.scrollHeight, innerHeight);
    const labels = [];
    const selectedIssues = new Set(), groups = new Map();
    const outcomes = targets.map(item => {
        const skipped = note => ({marker:null, note});
        if (selectedIssues.has(item.issue))
            return skipped('A different element is the screenshot example for this issue.');
        if (!item.target.length) return skipped('No element selector was supplied for this issue.');
        if (item.target.length !== 1)
            return skipped('Frame targets cannot be highlighted in the page screenshot.');
        const chain = Array.isArray(item.target[0]) ? item.target[0] : [item.target[0]];
        let scope = document, element = null;
        try {
            for (let i = 0; i < chain.length; i++) {
                const matches = scope.querySelectorAll(chain[i]);
                if (matches.length !== 1)
                    return skipped('The reported selector no longer identifies one element.');
                element = matches[0];
                if (i < chain.length - 1) {
                    if (!element.shadowRoot)
                        return skipped('The reported shadow-root target is not accessible.');
                    scope = element.shadowRoot;
                }
            }
        } catch {
            return skipped('The reported selector could not be located for the screenshot.');
        }
        if (!element) return skipped('No visible element was found for this issue.');
        const rect = element.getBoundingClientRect(), computed = getComputedStyle(element);
        if (!element.getClientRects().length || computed.visibility === 'hidden' ||
            computed.opacity === '0' || rect.width <= 0 || rect.height <= 0)
            return skipped('This issue has no visible element box to highlight.');
        const left = rect.left + scrollX, top = rect.top + scrollY;
        const edgeRight = rect.right + scrollX, edgeBottom = rect.bottom + scrollY;
        if (edgeRight <= 0 || edgeBottom <= 0 || left >= width || top >= height)
            return skipped('The affected element is outside the captured page area.');
        // Keep the border outside privacy masks without uncovering the element.
        const padding = 4;
        const x = Math.max(2, left - padding), y = Math.max(2, top - padding);
        const right = Math.min(width - 2, edgeRight + padding);
        const bottom = Math.min(height - 2, edgeBottom + padding);
        if (right <= x || bottom <= y)
            return skipped('The affected element is outside the captured page area.');
        const key = JSON.stringify(item.target);
        let group = groups.get(key);
        if (!group && groups.size >= 100)
            return skipped('The screenshot reached its limit of 100 highlighted targets.');
        if (!group) {
            group = {marker:groups.size + 1,x,y,right,bottom,violation:false,titles:[]};
            groups.set(key,group);
        }
        group.violation ||= item.violation;
        if (!group.titles.includes(item.label)) group.titles.push(item.label);
        selectedIssues.add(item.issue);
        return {marker:group.marker, note:['HTML','BODY'].includes(element.tagName) ?
            'Page-wide issue: this marker outlines the document.' : ''};
    });
    if (groups.size) {
        document.documentElement.append(root);
        root.showPopover(); // Keep boxes visible above modal dialogs and popovers.
    }
    for (const group of groups.values()) {
        const {marker,x,y,right,bottom} = group;
        const color = group.violation ? '#be123c' : '#9a5b00';
        const box = document.createElement('div');
        box.className = 'box';
        box.style.cssText = `--ink:${color};left:${x}px;top:${y}px;` +
            `width:${right-x}px;height:${bottom-y}px`;
        const label = document.createElement('div');
        label.className = 'label';
        for (const title of group.titles) {
            const issue = document.createElement('div');
            issue.className = 'issue';
            issue.textContent = '#' + marker + ' ' + title;
            label.append(issue);
        }
        label.style.cssText = `--ink:${color};width:max-content;` +
            `max-width:${Math.min(440,width-4)}px`;
        shadow.append(box,label);
        const labelRect = label.getBoundingClientRect();
        const labelWidth = labelRect.width, labelHeight = labelRect.height;
        const labelX = Math.min(x, Math.max(0, width - labelWidth - 2));
        let labelY = Math.max(2, y - labelHeight - 2);
        for (let attempt = 0; attempt < 100 && labels.some(previous =>
            labelX < previous.x + previous.width && labelX + labelWidth > previous.x &&
            labelY < previous.y + previous.height && labelY + labelHeight > previous.y);
            attempt++) {
            labelY = Math.min(height - labelHeight - 2, labelY + labelHeight + 2);
        }
        labels.push({x:labelX,y:labelY,width:labelWidth,height:labelHeight});
        label.style.left = `${labelX}px`;
        label.style.top = `${Math.max(2,labelY)}px`;
    }
    return outcomes;
}"""
