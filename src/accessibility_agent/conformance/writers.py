"""Portable WCAG review worksheets and scan comparisons, with no network dependencies."""

import hashlib
import json
import secrets
import shutil
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jinja2 import Environment, StrictUndefined

from accessibility_agent.conformance.models import DraftACR
from accessibility_agent.reporting.presentation import _local_evidence_path
from accessibility_agent.reporting.writers import atomic_write

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
COMPARISON_STATUSES = ("new", "still_present", "resolved", "not_verified", "newly_observed")


def _safe_url(value: str) -> str:
    """Only explicit HTTP(S) links may leave the local document on user activation."""
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() in {"http", "https"} and parts.netloc
                and parts.username is None and parts.password is None
                and not any(ord(char) < 33 or char == "\\" for char in value)):
            return value
    except ValueError:
        pass
    return ""


def _render(name: str, **context: Any) -> str:
    environment = Environment(autoescape=True, undefined=StrictUndefined)
    environment.filters["safe_url"] = _safe_url
    source = files("accessibility_agent.conformance").joinpath(
        "templates", name
    ).read_text(encoding="utf-8")
    return environment.from_string(source).render(nonce=secrets.token_urlsafe(24), **context)


def _copy_screenshots(
    document: DraftACR, output_dir: Path, source_dir: Path,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    screenshots: dict[str, dict[str, str]] = {}
    warnings = []
    for evidence in document.evidence:
        if evidence.kind != "screenshot":
            continue
        try:
            source = _local_evidence_path(source_dir, evidence.path)
            if source is None or source.suffix.lower() not in IMAGE_SUFFIXES:
                raise ValueError("unsafe or unsupported image path")
            if not source.is_file():
                raise ValueError("image file is missing")
            with source.open("rb") as stream:
                header = stream.read(16)
            valid_image = (
                source.suffix.lower() == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n")
                or source.suffix.lower() in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff")
                or source.suffix.lower() == ".webp" and header.startswith(b"RIFF")
                and header[8:12] == b"WEBP"
            )
            if not valid_image:
                raise ValueError("file does not have a supported image signature")
            # Generated names do not reuse untrusted path or evidence-id components.
            digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:24]
            relative = f"evidence/{digest}{source.suffix.lower()}"
            destination = _local_evidence_path(output_dir, relative)
            if destination is None:
                raise ValueError("image destination leaves the output directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source != destination:
                shutil.copyfile(source, destination)
            screenshots[evidence.evidence_id] = {
                "path": relative, "description": evidence.description,
            }
        except (OSError, ValueError) as exc:
            warnings.append(f"Screenshot {evidence.evidence_id} was not copied: {exc}.")
    return screenshots, warnings


def write_acr(document: DraftACR, output_dir: Path, source_dir: Path) -> Path:
    """Write an editable offline draft; only a validated rebuild changes the saved ACR."""
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshots, evidence_warnings = _copy_screenshots(document, output_dir, source_dir)
    portable = document.model_copy(deep=True)
    for evidence in portable.evidence:
        if evidence.evidence_id in screenshots:
            evidence.path = screenshots[evidence.evidence_id]["path"]
    html = _render(
        "acr.html", document=document, review_set=document.review_set().model_dump(mode="json"),
        screenshots=screenshots, evidence_warnings=evidence_warnings,
    )
    atomic_write(output_dir / "acr.json", portable.model_dump_json(indent=2))
    path = output_dir / "acr-report.html"
    atomic_write(path, html)
    return path


def write_comparison(comparison: dict[str, Any], output_dir: Path) -> Path:
    """Render supplied comparison facts without inferring remediation or conformance."""
    output_dir.mkdir(parents=True, exist_ok=True)
    context = {
        "baseline_scan_id": "Not recorded", "current_scan_id": "Not recorded",
        "counts": {}, "items": [], "warnings": [], "criterion_changes": [],
        **comparison,
    }
    html = _render("comparison.html", comparison=context, statuses=COMPARISON_STATUSES)
    atomic_write(
        output_dir / "comparison.json", json.dumps(comparison, indent=2, ensure_ascii=False)
    )
    path = output_dir / "comparison.html"
    atomic_write(path, html)
    return path
