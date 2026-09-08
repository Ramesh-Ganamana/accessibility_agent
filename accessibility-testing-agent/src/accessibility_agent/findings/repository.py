"""Group identical rule/target findings while retaining every occurrence."""

from collections.abc import Sequence

from accessibility_agent.models import Finding


class MemoryFindingRepository:
    def __init__(self) -> None:
        self._findings: dict[tuple[str, str, str, str], Finding] = {}

    def add(self, findings: Sequence[Finding]) -> None:
        for finding in findings:
            for occurrence in finding.occurrences:
                key = (finding.source, finding.rule_id, finding.classification, occurrence.selector)
                if key not in self._findings:
                    grouped = finding.model_copy(deep=True)
                    grouped.id = f"A11Y-{len(self._findings) + 1:04d}"
                    grouped.occurrences = [occurrence.model_copy(deep=True)]
                    self._findings[key] = grouped
                else:
                    grouped = self._findings[key]
                    if occurrence not in grouped.occurrences:
                        grouped.occurrences.append(occurrence.model_copy(deep=True))

    def all(self) -> Sequence[Finding]:
        return list(self._findings.values())
