import asyncio
import io
import json
import re
from datetime import UTC, date, datetime
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from accessibility_agent.conformance.automatic import prepare_report_bundle
from accessibility_agent.conformance.builder import report_digest
from accessibility_agent.conformance.models import CriterionReview, ReviewSet
from accessibility_agent.conformance.vpat import template_bytes
from accessibility_agent.models import (
    ApplicationInfo,
    Finding,
    Occurrence,
    Report,
    RuleResult,
    ScanMetadata,
    ScanResult,
)
from accessibility_agent.reporting.writers import HTMLReportWriter


def report_for(wcag="2.2-AA"):
    return Report(
        application=ApplicationInfo(url="https://example.test/", title="Example <product>"),
        scan=ScanMetadata(
            scan_id="normal-scan", started_at=datetime(2026, 9, 10, tzinfo=UTC),
            status="completed", wcag=wcag,
        ),
        results=[ScanResult(
            state_id="home", engine="axe-core", engine_version="4.10.3",
            rules=[RuleResult(rule_id="button-name", outcome="violation", tags=["wcag412"])],
        )],
        findings=[Finding(
            id="button-one", title="Buttons need names", rule_id="button-name", source="AXE",
            classification="VIOLATION", wcag=["4.1.2"], occurrences=[Occurrence(
                url="https://example.test/", state_id="home", element="button", selector="#button",
            )],
        )],
    )


def test_normal_report_includes_acr_and_wcag_preview_without_mutating_scan(tmp_path):
    report = report_for()
    before = report.model_dump_json()
    html = asyncio.run(HTMLReportWriter().write(report, tmp_path)).read_text(encoding="utf-8")
    relative = "conformance/" + report_digest(report)
    directory = tmp_path / relative
    review = ReviewSet.model_validate_json((directory / "review.json").read_text("utf-8"))
    assert len(review.criteria) == 55
    assert all(row.conformance is None for row in review.criteria)
    assert review.product.version.startswith("Not provided")
    assert all(not getattr(review.product, field) for field in (
        "organization", "environment", "user_role"
    ))
    assert f'href="{relative}/acr-report.html"' in html
    assert f'href="{relative}/acr.json"' in html
    assert 'href="#vpat-acr"' in html
    assert 'id="vpat-criteria"' in html
    assert "4.1.2 Name, Role, Value" in html
    assert "1 mapped finding" in html
    assert "Example &lt;product&gt;" in html
    assert report.model_dump_json() == before


def test_regeneration_preserves_matching_reviewer_input_exactly(tmp_path):
    report = report_for()
    first = prepare_report_bundle(report, tmp_path)
    path = tmp_path / first.directory / "review.json"
    review = ReviewSet.model_validate_json(path.read_text("utf-8"))
    review.product.notes = "Reviewer notes must survive regeneration"
    review.criteria[0] = CriterionReview(
        criterion_id=review.criteria[0].criterion_id, conformance="Partially Supports",
        reviewer="Example tester", reviewed_on=date(2026, 9, 10),
        methods="Manual keyboard and screen-reader checks", remarks="Test scope verified",
        evidence=["Review record 123"],
    )
    path.write_text(review.model_dump_json(indent=4), encoding="utf-8")
    before = path.read_bytes()
    second = prepare_report_bundle(report, tmp_path)
    assert second.error == ""
    assert second.directory == first.directory
    assert path.read_bytes() == before
    assert second.document.pending_count == 54
    assert second.document.product.notes == review.product.notes


@pytest.mark.parametrize("invalid", ["malformed", "wrong-source", "missing-criterion"])
def test_bad_review_never_overwrites_existing_bundle_or_breaks_report(tmp_path, invalid):
    report = report_for()
    first = prepare_report_bundle(report, tmp_path)
    directory = tmp_path / first.directory
    path = directory / "review.json"
    if invalid == "malformed":
        path.write_text("private broken review {", encoding="utf-8")
    else:
        data = json.loads(path.read_text("utf-8"))
        if invalid == "wrong-source":
            data["source_sha256"] = "b" * 64
        else:
            data["criteria"].pop()
        path.write_text(json.dumps(data), encoding="utf-8")
    before = {file.name: file.read_bytes() for file in directory.iterdir() if file.is_file()}
    html = asyncio.run(HTMLReportWriter().write(report, tmp_path)).read_text(encoding="utf-8")
    assert "saved review.json is invalid or belongs to a different scan" in html
    assert "private broken" not in html
    assert "Crawl Results" in html
    after = {file.name: file.read_bytes() for file in directory.iterdir() if file.is_file()}
    assert before == after


def test_modified_scan_has_separate_review_directory(tmp_path):
    report = report_for()
    first = prepare_report_bundle(report, tmp_path)
    first_path = tmp_path / first.directory / "review.json"
    original = first_path.read_bytes()
    report.application.title = "New title"
    second = prepare_report_bundle(report, tmp_path)
    assert first.directory != second.directory
    assert first_path.read_bytes() == original
    assert (tmp_path / second.directory / "review.json").exists()


def test_unsupported_wcag_retains_ordinary_report(tmp_path):
    html = asyncio.run(HTMLReportWriter().write(report_for("3.0-AA"), tmp_path)).read_text("utf-8")
    assert "VPAT/ACR preparation unavailable" in html
    assert "Crawl Results" in html
    assert not (tmp_path / "conformance").exists()


def test_export_failure_isolated_from_report_and_private_error_not_leaked(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("private server details")

    monkeypatch.setattr("accessibility_agent.conformance.automatic.write_acr", fail)
    report = report_for()
    html = asyncio.run(HTMLReportWriter().write(report, tmp_path)).read_text("utf-8")
    assert "VPAT/ACR files could not be generated (OSError)" in html
    assert "private server" not in html
    assert "Findings" in html
    assert (tmp_path / "conformance" / report_digest(report) / "review.json").exists()


def test_both_docx_downloads_are_linked_when_present(tmp_path, monkeypatch):
    from accessibility_agent.conformance.automatic import write_acr

    def export(document, output_dir, source_dir):
        result = write_acr(document, output_dir, source_dir)
        (output_dir / "vpat-template.docx").write_bytes(b"fixture official template")
        (output_dir / "draft-acr.docx").write_bytes(b"fixture draft")
        return result

    monkeypatch.setattr("accessibility_agent.conformance.automatic.write_acr", export)
    report = report_for()
    html = asyncio.run(HTMLReportWriter().write(report, tmp_path)).read_text("utf-8")
    relative = "conformance/" + report_digest(report)
    assert f'href="{relative}/vpat-template.docx" download' in html
    assert f'href="{relative}/draft-acr.docx" download' in html


def test_bundle_directory_cannot_escape_report_directory(tmp_path):
    report = report_for()
    output = tmp_path / "report"
    output.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (output / "conformance").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are unavailable for this Windows account")
    bundle = prepare_report_bundle(report, output)
    assert "outside the report folder" in bundle.error
    assert not list(outside.iterdir())


@pytest.mark.parametrize("wcag, count", [("2.0-A", 25), ("2.1-AAA", 78), ("2.2-AA", 55)])
def test_word_draft_preserves_template_parts_and_selected_pending_criteria(tmp_path, wcag, count):
    bundle = prepare_report_bundle(report_for(wcag), tmp_path)
    assert not bundle.error
    directory = tmp_path / bundle.directory
    source_bytes = template_bytes()
    assert (directory / "vpat-template.docx").read_bytes() == source_bytes
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with (
        ZipFile(io.BytesIO(source_bytes)) as source,
        ZipFile(directory / "draft-acr.docx") as draft,
    ):
        assert draft.testzip() is None
        assert source.namelist() == draft.namelist()
        for name in source.namelist():
            if name not in {"word/document.xml", "word/settings.xml"}:
                assert source.read(name) == draft.read(name)
        document = ElementTree.fromstring(draft.read("word/document.xml"))
        criteria = []
        for row in document.findall(".//w:tr", namespace):
            cells = row.findall("w:tc", namespace)
            texts = ["".join(cell.itertext()) for cell in cells]
            if texts and (match := re.match(r"\d+\.\d+\.\d+", texts[0])):
                criteria.append(match.group())
                assert not texts[1].strip()  # Includes the source template's prefilled 4.1.1.
                assert "Pending human review" in texts[2]
        assert len(criteria) == len(set(criteria)) == count
        assert set(criteria) == {row.criterion_id for row in bundle.document.criteria}
        assert "Example <product>" in "".join(document.itertext())
        settings = ElementTree.fromstring(draft.read("word/settings.xml"))
        update = settings.find("w:updateFields", namespace)
        assert update is not None
        assert update.get("{" + namespace["w"] + "}val") == "true"
