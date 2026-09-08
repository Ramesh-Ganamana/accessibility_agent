"""Offline, escaped reports. Atomic replacement avoids partially written report files."""

import json
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from accessibility_agent.models import Report


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


class JSONReportWriter:
    async def write(self, report: Report, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "results.json"
        atomic_write(path, report.model_dump_json(indent=2))
        atomic_write(
            output_dir / "graph.json", json.dumps(report.graph.model_dump(mode="json"), indent=2)
        )
        return path


class HTMLReportWriter:
    async def write(self, report: Report, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        source = (
            files("accessibility_agent.reporting")
            .joinpath("templates/report.html")
            .read_text(encoding="utf-8")
        )
        template = Environment(autoescape=True, undefined=StrictUndefined).from_string(source)
        path = output_dir / "accessibility-report.html"
        atomic_write(
            path, template.render(report=report, percentages=report.coverage.percentages())
        )
        return path
