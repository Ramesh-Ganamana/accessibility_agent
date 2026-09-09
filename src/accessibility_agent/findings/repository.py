"""One issue group per rule and impact, retaining every distinct affected observation."""

import json
from collections.abc import Sequence

from accessibility_agent.models import Finding, Occurrence


def observation_key(occurrence: Occurrence) -> str:
    """Evidence references can grow without creating another identical observation."""
    return json.dumps(occurrence.model_dump(exclude={"evidence"}), sort_keys=True)


class MemoryFindingRepository:
    def __init__(self) -> None:
        self._findings: dict[tuple[str, str, str, str | None], Finding] = {}
        self._observations: dict[str, dict[str, Occurrence]] = {}

    def add(self, findings: Sequence[Finding]) -> None:
        for finding in findings:
            key = (
                finding.source,
                finding.rule_id,
                finding.classification,
                finding.rule_impact,
            )
            if key not in self._findings:
                grouped = finding.model_copy(deep=True)
                grouped.id = f"A11Y-{len(self._findings) + 1:04d}"
                first = finding.occurrences[0].model_copy(deep=True)
                grouped.occurrences = [first]
                self._findings[key] = grouped
                self._observations[grouped.id] = {observation_key(first): first}
            grouped = self._findings[key]
            observations = self._observations[grouped.id]
            for occurrence in finding.occurrences:
                identity = observation_key(occurrence)
                if identity not in observations:
                    observations[identity] = occurrence.model_copy(deep=True)
                    grouped.occurrences.append(observations[identity])
                else:
                    existing = observations[identity]
                    existing.evidence = list(
                        dict.fromkeys([*existing.evidence, *occurrence.evidence])
                    )

    def all(self) -> Sequence[Finding]:
        return list(self._findings.values())
