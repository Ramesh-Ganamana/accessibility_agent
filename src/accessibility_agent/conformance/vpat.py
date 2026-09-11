"""Fill the actual ITI WCAG template without a Word dependency or network access.

The original download is immutable. Only report slots and field-refresh settings
are edited in a copy; all other OOXML parts remain byte-identical to ITI's source.
"""

from __future__ import annotations

import hashlib
import io
import re
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, cast
from xml.dom import Node
from xml.dom.minidom import Document, Element, parseString
from zipfile import ZIP_DEFLATED, ZipFile

from accessibility_agent.conformance.catalog import criteria_for

if TYPE_CHECKING:
    from accessibility_agent.conformance.models import CriterionAssessment, DraftACR

TEMPLATE_NAME = "VPAT-2.5Rev-WCAG.docx"
TEMPLATE_SHA256 = "d9028314001743473e30e9615e2593fb14d56d3a87ee15e7dda17e0d134470a0"
TEMPLATE_URL = "https://www.itic.org/dotAsset/67270ffc-91e8-4812-9481-8067621f24fd.docx"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@lru_cache(maxsize=1)
def template_bytes() -> bytes:
    data = files("accessibility_agent.conformance").joinpath("vendor", TEMPLATE_NAME).read_bytes()
    if hashlib.sha256(data).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("The bundled official VPAT template failed its integrity check")
    return data


def _children(parent: Element, name: str) -> list[Element]:
    return [child for child in parent.childNodes
            if child.nodeType == Node.ELEMENT_NODE and child.nodeName == "w:" + name]


def _text(element: Element) -> str:
    return "".join(child.data for text in element.getElementsByTagName("w:t")
                   for child in text.childNodes if child.nodeType == Node.TEXT_NODE)


def _new(doc: Document, name: str) -> Element:
    return doc.createElementNS(WORD_NS, "w:" + name)


def _clean(value: str) -> str:
    # XML text is data, never markup or Word field instructions. Strip illegal
    # XML 1.0 controls without losing ordinary non-Latin text or line breaks.
    return "".join(char for char in value if char in "\t\n\r" or
                   0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or
                   0x10000 <= ord(char) <= 0x10FFFF)


def _set_paragraph(paragraph: Element, value: str) -> None:
    doc = paragraph.ownerDocument
    assert doc is not None
    runs = _children(paragraph, "r")
    properties = _children(runs[0], "rPr") if runs else []
    run = _new(doc, "r")
    if properties:
        run.appendChild(cast(Element, properties[0].cloneNode(True)))
    for child in list(paragraph.childNodes):
        if child.nodeName != "w:pPr":
            paragraph.removeChild(child)
    for index, line in enumerate(_clean(value).splitlines() or [""]):
        if index:
            run.appendChild(_new(doc, "br"))
        text = _new(doc, "t")
        text.setAttribute("xml:space", "preserve")
        text.appendChild(doc.createTextNode(line))
        run.appendChild(text)
    paragraph.appendChild(run)


def _value_paragraph(doc: Document, value: str) -> Element:
    paragraph = _new(doc, "p")
    props = _new(doc, "pPr")
    spacing = _new(doc, "spacing")
    spacing.setAttribute("w:after", "120")
    props.appendChild(spacing)
    paragraph.appendChild(props)
    _set_paragraph(paragraph, value)
    run = _children(paragraph, "r")[0]
    run_props = _new(doc, "rPr")
    size = _new(doc, "sz")
    size.setAttribute("w:val", "22")
    run_props.appendChild(size)
    run.insertBefore(run_props, run.firstChild)
    return paragraph


def _set_cell(cell: Element, value: str) -> None:
    paragraphs = _children(cell, "p")
    if not paragraphs:
        doc = cell.ownerDocument
        assert doc is not None
        paragraph = _new(doc, "p")
        cell.appendChild(paragraph)
        paragraphs = [paragraph]
    _set_paragraph(paragraphs[0], value)
    for paragraph in paragraphs[1:]:
        cell.removeChild(paragraph)


def _remarks(row: CriterionAssessment | None) -> str:
    if row is None:
        return "Pending human review. No criterion assessment was supplied."
    review = row.review
    lines = [review.remarks] if review.remarks.strip() else []
    if review.conformance is None:
        lines.insert(0, "Pending human review. No conformance decision has been recorded.")
    else:
        lines.append(f"Reviewer: {review.reviewer}; reviewed: {review.reviewed_on}.")
    if review.methods.strip():
        lines.append("Evaluation methods: " + review.methods)
    if review.evidence:
        lines.append("Reviewer evidence: " + "; ".join(review.evidence))
    lines.extend("CONFLICT: " + conflict for conflict in row.conflicts)
    if row.observations:
        lines.append("Automated observations (not a conformance decision): " + ", ".join(
            f"{name}: {count}" for name, count in sorted(row.observations.items())
        ))
    for finding in row.findings[:10]:
        lines.append(
            f"Finding {finding.finding_id}: {finding.title} ({finding.rule_id}); "
            f"{finding.classification}; occurrences: {finding.occurrence_count}."
        )
        if finding.evidence_ids:
            lines.append("Evidence IDs: " + ", ".join(finding.evidence_ids[:10]))
    if row.findings:
        lines.append("See acr.json and the HTML review for full finding locations and evidence.")
    if len(row.findings) > 10:
        lines.append(f"{len(row.findings) - 10} additional findings are listed in acr.json.")
    return "\n".join(lines)


def _fill_document(source: bytes, document: DraftACR) -> bytes:
    doc = parseString(source)
    body = doc.getElementsByTagName("w:body")[0]
    heading = next((p for p in _children(body, "p")
                    if _text(p) == "[Company] Accessibility Conformance Report"), None)
    if heading is None:
        raise ValueError("Official VPAT report title is missing")
    for child in list(body.childNodes):
        if child is heading:
            break
        body.removeChild(child)
    _set_paragraph(heading, (document.product.organization or "Company not provided")
                   + " Accessibility Conformance Report")
    product = document.product
    notes = (
        "DRAFT FOR HUMAN REVIEW. This is not a completed ACR or certification. "
        "Blank conformance cells mean pending review, not Supports or a VPAT conformance term. "
        "Complete missing product metadata and all selected criteria before publication. "
        f"Scope: {product.scope}. Environment: {product.environment or 'Not provided'}. "
        f"User role: {product.user_role or 'Not provided'}. "
        f"Source scan: {document.source_scan_id} ({document.scan_status}); "
        f"WCAG {document.wcag_version} through Level {document.level}. "
        "Only the selected WCAG version and levels are included; other levels and standards "
        "are outside this draft. Automated observations do not establish conformance. "
        "Verify Word layout and refresh page-number fields before publication."
    )
    values = {
        "Name of Product/Version:": f"{product.name} / {product.version}",
        "Report Date:": (
            f"{document.report_date.day} {document.report_date:%B %Y} (draft generated)"
        ),
        "Product Description:": product.description or "Not provided - complete before publication",
        "Contact Information:": product.contact or "Not provided - complete before publication.",
        "Notes:": notes + ("\n" + product.notes if product.notes else ""),
        "Evaluation Methods Used:": "\n".join(document.evaluation_methods)
        or "No evaluation methods recorded.",
    }
    for paragraph in list(_children(body, "p")):
        text = _text(paragraph).strip()
        if text in values:
            body.insertBefore(
                _value_paragraph(doc, values.pop(text)), cast(Element | None, paragraph.nextSibling)
            )
        elif text == "WCAG 2.x Report":
            _set_paragraph(paragraph, f"WCAG {document.wcag_version} Report")
        elif text in {"Legal Disclaimer (Company)",
                      "Include your company legal disclaimer here, if needed."}:
            body.removeChild(paragraph)
    if values:
        raise ValueError("Official VPAT metadata slots are missing")

    catalog = {criterion.id: criterion for criterion in criteria_for(
        document.wcag_version, document.level
    )}
    assessments = {row.criterion_id: row for row in document.criteria}
    found: set[str] = set()
    levels = ("A", "AA", "AAA")
    for table in list(_children(body, "tbl")):
        rows = _children(table, "tr")
        first_cells = _children(rows[0], "tc")
        if _text(first_cells[0]) == "Standard/Guideline":
            for row in rows[1:]:
                cells = _children(row, "tc")
                if not _text(cells[0]).endswith(document.wcag_version):
                    table.removeChild(row)
                    continue
                _set_cell(cells[1], "\n".join(
                    f"Level {level} "
                    f"({'Yes' if levels.index(level) <= levels.index(document.level) else 'No'})"
                    for level in levels
                ))
            continue
        if _text(first_cells[0]) != "Criteria":
            continue
        for row in rows[1:]:
            cells = _children(row, "tc")
            match = re.match(r"\d+\.\d+\.\d+", _text(cells[0]))
            if not match:
                raise ValueError("Unexpected official VPAT criterion row")
            criterion_id = match.group()
            if criterion_id not in catalog:
                table.removeChild(row)
                continue
            found.add(criterion_id)
            assessment = assessments.get(criterion_id)
            _set_cell(cells[1], (assessment.review.conformance or "") if assessment else "")
            _set_cell(cells[2], _remarks(assessment))
        if len(_children(table, "tr")) == 1:
            # ITI permits removal of entire unselected level sections.
            preceding = table.previousSibling
            while preceding and preceding.nodeName == "w:p":
                previous = preceding.previousSibling
                text = _text(cast(Element, preceding))
                body.removeChild(cast(Element, preceding))
                if text.startswith("Table "):
                    break
                preceding = previous
            body.removeChild(table)
        else:
            properties = _children(rows[0], "trPr")
            props = properties[0] if properties else _new(doc, "trPr")
            if not properties:
                rows[0].insertBefore(props, rows[0].firstChild)
            if not _children(props, "tblHeader"):
                props.appendChild(_new(doc, "tblHeader"))
    if found != set(catalog):
        raise ValueError("Official VPAT table does not contain every selected WCAG criterion")
    return doc.toxml(encoding="utf-8")


def build_vpat_docx(document: DraftACR) -> bytes:
    """Generate a draft in the official form; never infer reviewer decisions."""
    output = io.BytesIO()
    with ZipFile(io.BytesIO(template_bytes())) as template, ZipFile(
        output, "w", compression=ZIP_DEFLATED
    ) as result:
        for info in template.infolist():
            data = template.read(info.filename)
            if info.filename == "word/document.xml":
                data = _fill_document(data, document)
            elif info.filename == "word/settings.xml":
                settings = parseString(data)
                fields = settings.getElementsByTagName("w:updateFields")
                update = fields[0] if fields else _new(settings, "updateFields")
                update.setAttribute("w:val", "true")
                if not fields:
                    assert settings.documentElement is not None
                    settings.documentElement.appendChild(update)
                data = settings.toxml(encoding="utf-8")
            result.writestr(info, data)
    return output.getvalue()


def write_vpat_exports(document: DraftACR, output_dir: Path) -> None:
    """Export two explicit filenames; never use product metadata as filesystem paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    exports = {
        "vpat-template.docx": template_bytes(),
        "draft-acr.docx": build_vpat_docx(document),
    }
    for name, data in exports.items():
        path = output_dir / name
        if path.is_symlink() or path.with_suffix(".docx.tmp").is_symlink():
            raise ValueError("VPAT export target must not be a symbolic link")
        temporary = path.with_suffix(".docx.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
