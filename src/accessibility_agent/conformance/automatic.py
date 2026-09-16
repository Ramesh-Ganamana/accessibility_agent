"""Attach an offline, source-bound VPAT/ACR bundle to an ordinary scan report."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from accessibility_agent.conformance.builder import build_acr, prepare_review, report_digest
from accessibility_agent.conformance.models import DraftACR, Product, ReviewSet
from accessibility_agent.conformance.writers import write_acr
from accessibility_agent.models import Report


@dataclass
class ReportBundle:
    directory: str = ""
    document: DraftACR | None = None
    links: dict[str, str] = field(default_factory=dict)
    error: str = ""


def _initial_product(report: Report) -> Product:
    """Record only observed identity; absent product facts remain explicit placeholders."""
    url = report.application.url
    return Product(
        application_id="scan-target-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:20],
        name=report.application.title.strip() or url or "Unnamed scan target",
        version="Not provided — enter the tested product version",
        scope="Automatically sampled pages and states in this scan; owner must confirm scope",
        notes="Prepared automatically from scan observations. Product version, organization, "
        "contact, environment and user role were not supplied. Complete these in the review "
        "editor and verify the generated application identity before comparing releases.",
    )


def _checked_directory(output_dir: Path, relative: str) -> Path:
    """Do not follow redirected bundle files or temporary files outside the report."""
    root = output_dir.resolve()
    directory = output_dir / relative
    if not directory.resolve().is_relative_to(root):
        raise ValueError("The conformance output directory is outside the report folder")
    for filename in (
        "review.json", "acr.json", "acr-report.html", "vpat-template.docx", "draft-acr.docx",
    ):
        for name in (filename, filename + ".tmp"):
            path = directory / name
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError(
                    "A conformance output file is redirected; existing files preserved"
                )
    return directory


def prepare_report_bundle(report: Report, output_dir: Path) -> ReportBundle:
    """Never replace reviewer input or turn an export failure into a failed crawl report."""
    relative = "conformance/" + report_digest(report)
    bundle = ReportBundle(directory=relative)
    try:
        directory = _checked_directory(output_dir, relative)
        review_path = directory / "review.json"
        if review_path.exists():
            try:
                review = ReviewSet.model_validate_json(review_path.read_text(encoding="utf-8"))
                document = build_acr(report, review)
            except (ValueError, OSError):
                bundle.error = (
                    "The saved review.json is invalid or belongs to a different scan. "
                    "No conformance files were replaced. Correct the review or move the "
                    "bundle aside, then regenerate the report."
                )
                return bundle
        else:
            review = prepare_review(report, _initial_product(report))
            document = build_acr(report, review)
            directory.mkdir(parents=True, exist_ok=True)
            # Exclusive creation protects reviewer work even if another writer races this one.
            with review_path.open("x", encoding="utf-8") as stream:
                stream.write(review.model_dump_json(indent=2))
        write_acr(document, directory, output_dir)
        bundle.document = document
        for key, filename in (
            ("template", "vpat-template.docx"), ("draft", "draft-acr.docx"),
            ("editor", "acr-report.html"), ("json", "acr.json"), ("review", "review.json"),
        ):
            if (directory / filename).is_file():
                bundle.links[key] = relative + "/" + filename
    except ValidationError:
        bundle.error = "VPAT/ACR preparation could not validate the scan or product metadata."
    except ValueError as exc:
        # Builder errors contain controlled explanations, never invalid review JSON contents.
        bundle.error = f"VPAT/ACR preparation unavailable: {exc}. The scan report is preserved."
    except Exception as exc:
        # Export is an optional report attachment. Avoid exposing paths or private input in errors.
        bundle.error = (
            f"VPAT/ACR files could not be generated ({type(exc).__name__}). "
            "Check that the report folder is writable and close open export files, then "
            "regenerate the report. Existing review.json is preserved."
        )
    return bundle
