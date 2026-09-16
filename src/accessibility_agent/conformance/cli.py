"""Offline preparation, review and comparison; never starts a browser or sends data."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from accessibility_agent.conformance.builder import (
    build_acr,
    compare_assessments,
    prepare_review,
)
from accessibility_agent.conformance.comparison import compare_reports
from accessibility_agent.conformance.models import Product, ReviewSet
from accessibility_agent.conformance.writers import write_acr, write_comparison
from accessibility_agent.models import Report
from accessibility_agent.reporting.writers import atomic_write


def _report(path: Path) -> Report:
    return Report.model_validate_json(path.read_text(encoding="utf-8"))


def _review(path: Path) -> ReviewSet:
    return ReviewSet.model_validate_json(path.read_text(encoding="utf-8"))


def _protect_inputs(output: Path, inputs: list[Path]) -> None:
    targets = [output / name for name in (
        "acr.json", "acr-report.html", "review.json", "comparison.json", "comparison.html",
        "draft-acr.docx", "vpat-template.docx",
    )]
    if {path.resolve() for path in inputs} & {path.resolve() for path in targets}:
        raise ValueError("Output would overwrite an input file; choose a separate output folder")


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="accessibility-agent acr",
        description="WCAG draft ACR preparation and release comparisons (offline, human review)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Create draft ACR and editable review worksheet")
    prepare.add_argument("--report", type=Path, required=True, help="Existing scan results.json")
    prepare.add_argument("--application-id", required=True, help="Stable app/environment/role ID")
    prepare.add_argument("--product-name", required=True)
    prepare.add_argument("--product-version", required=True)
    prepare.add_argument("--scope", required=True, help="Pages and complete processes evaluated")
    prepare.add_argument("--organization", default="")
    prepare.add_argument("--description", default="")
    prepare.add_argument("--contact", default="")
    prepare.add_argument("--environment", default="")
    prepare.add_argument("--user-role", default="")
    prepare.add_argument("--output", type=Path, required=True, help="New draft output folder")
    build = commands.add_parser("build", help="Validate reviewer input and refresh draft exports")
    build.add_argument("--report", type=Path, required=True)
    build.add_argument("--review", type=Path, required=True, help="Completed/exported review JSON")
    build.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare", help="Compare two scans and optional reviewed ACRs")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--current", type=Path, required=True)
    compare.add_argument("--baseline-review", type=Path)
    compare.add_argument("--current-review", type=Path)
    compare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            if bool(args.baseline_review) != bool(args.current_review):
                raise ValueError("Supply both baseline and current review files, or neither")
            inputs = [args.baseline, args.current]
            if args.baseline_review:
                inputs.extend([args.baseline_review, args.current_review])
            _protect_inputs(args.output, inputs)
            baseline, current = _report(args.baseline), _report(args.current)
            comparison = compare_reports(baseline, current)
            comparison["baseline_coverage"] = baseline.coverage.model_dump()
            comparison["current_coverage"] = current.coverage.model_dump()
            if args.baseline_review:
                old_acr = build_acr(baseline, _review(args.baseline_review))
                new_acr = build_acr(current, _review(args.current_review))
                comparison["criterion_changes"] = compare_assessments(old_acr, new_acr)
                comparison["products"] = {
                    "baseline": old_acr.product.model_dump(mode="json"),
                    "current": new_acr.product.model_dump(mode="json"),
                }
            path = write_comparison(comparison, args.output)
            print(f"Comparison: {path.resolve()}")
            print(json.dumps(comparison["counts"], sort_keys=True))
            return 0

        report = _report(args.report)
        if args.command == "prepare":
            _protect_inputs(args.output, [args.report])
            if any((args.output / name).exists() for name in (
                "acr.json", "acr-report.html", "review.json", "draft-acr.docx", "vpat-template.docx"
            )):
                raise ValueError("Draft already exists; use a new folder or the build command")
            product = Product(
                application_id=args.application_id, name=args.product_name,
                version=args.product_version, scope=args.scope,
                organization=args.organization, description=args.description, contact=args.contact,
                environment=args.environment, user_role=args.user_role,
            )
            review = prepare_review(report, product)
        else:
            # The review input can be output/review.json; build never overwrites that file.
            _protect_inputs(args.output, [args.report])
            if args.review.resolve() in {
                (args.output / name).resolve() for name in (
                    "acr.json", "acr-report.html", "draft-acr.docx", "vpat-template.docx"
                )
            }:
                raise ValueError("Output would overwrite the review input")
            review = _review(args.review)
        document = build_acr(report, review)
        path = write_acr(document, args.output, args.report.parent)
        if args.command == "prepare":
            atomic_write(args.output / "review.json", review.model_dump_json(indent=2))
        print(f"Draft ACR: {path.resolve()}")
        print(f"VPAT-based Word draft: {(args.output / 'draft-acr.docx').resolve()}")
        print(f"Pending criteria: {document.pending_count}; conflicts: {document.conflict_count}")
        print("Draft only. Human review is required; this is not accessibility certification.")
        return 3 if document.conflict_count else 0
    except ValidationError:
        print("Invalid input schema or review metadata. Check required fields and review terms.")
        return 2
    except ValueError as error:
        print(str(error))
        return 2
    except OSError:
        print("Cannot read inputs or write outputs. Check paths and file permissions.")
        return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
