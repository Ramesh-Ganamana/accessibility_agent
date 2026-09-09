import base64
import json
import re
from datetime import UTC, datetime

import pytest

from accessibility_agent.conformance.models import (
    CriterionAssessment,
    CriterionReview,
    DraftACR,
    FindingReference,
    Product,
)
from accessibility_agent.conformance.writers import _safe_url, write_acr, write_comparison
from accessibility_agent.models import Evidence

PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


def draft(evidence=()):
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    return DraftACR(
        generated_at=now,
        report_date=now.date(),
        source_scan_id="scan-1",
        source_sha256="a" * 64,
        application_url="https://example.test/",
        scan_status="completed",
        product=Product(application_id="example", name="Example", version="1.0", scope="Home page"),
        wcag_version="2.2",
        level="AAA",
        evaluation_methods=["Automated axe-core"],
        coverage={},
        limitations=["Tested states only."],
        evidence=list(evidence),
        unmapped_finding_ids=[],
        criteria=[
            CriterionAssessment(
                criterion_id=identifier,
                title=title,
                level=level,
                url="https://www.w3.org/TR/WCAG22/#non-text-content",
                automated_status="not_tested",
                observations={},
                findings=[],
                checklist=["Evaluate with a screen reader."],
                review=CriterionReview(criterion_id=identifier),
            )
            for identifier, title, level in [
                ("1.1.1", "Non-text Content", "A"),
                ("1.4.3", "Contrast (Minimum)", "AA"),
                ("1.4.6", "Contrast (Enhanced)", "AAA"),
            ]
        ],
    )


def test_offline_acr_renderer_escapes_data_and_preserves_pending_decisions(tmp_path):
    document = draft()
    unsafe = '</script><script>alert("unsafe")</script>'
    document.product.name = unsafe
    document.criteria[0].title = unsafe
    document.criteria[0].url = "javascript:alert(1)"
    document.criteria[0].observations = {"pass": 14, "inapplicable": 6}
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    (output / "review.json").write_text("reviewer-owned", encoding="utf-8")
    html = write_acr(document, output, source).read_text(encoding="utf-8")
    assert unsafe not in html
    assert "&lt;/script&gt;&lt;script&gt;" in html
    assert "\\u003c/script\\u003e" in html
    assert 'href="javascript:' not in html
    assert html.count('<option value="" selected>Pending review</option>') == 3
    assert html.count('<option value="Not Evaluated">Not Evaluated</option>') == 2  # filter + AAA
    assert "Automated observations and zero reported issues do not establish Supports" in html
    assert "DRAFT · HUMAN REVIEW REQUIRED" in html
    assert "not an official ITI VPAT" in html
    assert "Pass: 14" in html
    assert "Manual evaluation checklist" in html
    nonce = re.search(r"script-src 'nonce-([^']+)'", html).group(1)
    assert f'<script nonce="{nonce}">' in html
    assert html.count("<script ") == 1
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert (output / "review.json").read_text(encoding="utf-8") == "reviewer-owned"
    saved = json.loads((output / "acr.json").read_text(encoding="utf-8"))
    assert saved["kind"] == "DRAFT_ACR"
    assert all(row["review"]["conformance"] is None for row in saved["criteria"])


def test_acr_copies_only_confined_allowlisted_screenshots(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "valid.png").write_bytes(PIXEL)
    (source / "unsafe.svg").write_text('<svg onload="alert(1)"/>', encoding="utf-8")
    (tmp_path / "outside.png").write_bytes(PIXEL)
    paths = [
        "valid.png",
        "../outside.png",
        "%2e%2e/outside.png",
        "%252e%252e/outside.png",
        "unsafe.svg",
        "javascript:alert(1)",
        str(tmp_path / "outside.png"),
        "https://example.test/image.png",
        "missing.png",
        "nul\x00.png",
    ]
    evidence = [
        Evidence(
            evidence_id=f"image-{index}",
            state_id="state-1",
            kind="screenshot",
            path=path,
            description='<img src=x onerror="alert(1)">',
        )
        for index, path in enumerate(paths)
    ]
    document = draft(evidence)
    document.criteria[0].findings = [
        FindingReference(
            finding_id="finding-1",
            rule_id="image-alt",
            title="Missing alt",
            classification="VIOLATION",
            source="AXE",
            occurrence_count=3,
            urls=["javascript:alert(1)"],
            selectors=["<img>"],
            evidence_ids=[item.evidence_id for item in evidence],
        )
    ]
    original = document.model_dump_json()
    output = tmp_path / "output"
    html = write_acr(document, output, source).read_text(encoding="utf-8")
    copied = list((output / "evidence").iterdir())
    assert len(copied) == 1 and copied[0].read_bytes() == PIXEL
    assert html.count("<img loading=") == 1
    assert 'href="javascript:' not in html
    assert 'src="../' not in html
    assert 'src="unsafe.svg"' not in html
    assert "&lt;img src=x onerror=" in html
    assert "3 occurrences" in html
    assert "Screenshot image-1 was not copied" in html
    assert document.model_dump_json() == original
    saved = json.loads((output / "acr.json").read_text(encoding="utf-8"))
    assert (output / saved["evidence"][0]["path"]).read_bytes() == PIXEL


def test_screenshot_symlink_cannot_escape_source_directory(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    external = tmp_path / "outside.png"
    external.write_bytes(PIXEL)
    try:
        (source / "linked.png").symlink_to(external)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this host")
    document = draft(
        [
            Evidence(
                evidence_id="link",
                state_id="state-1",
                kind="screenshot",
                path="linked.png",
            )
        ]
    )
    output = tmp_path / "output"
    html = write_acr(document, output, source).read_text(encoding="utf-8")
    assert "Screenshot link was not copied" in html
    assert not (output / "evidence").exists()


def test_conflicting_review_is_visible_and_saved_without_becoming_certified(tmp_path):
    document = draft()
    document.criteria[0].review = CriterionReview(
        criterion_id="1.1.1",
        conformance="Supports",
        reviewer="Reviewer",
        reviewed_on="2026-09-09",
        methods="Manual screen-reader evaluation",
        remarks="Review before sharing",
        evidence=["t-1"],
    )
    document.criteria[0].conflicts = ["Supports conflicts with a recorded violation."]
    html = write_acr(document, tmp_path, tmp_path).read_text(encoding="utf-8")
    assert "Conflicting review evidence" in html
    assert "Supports conflicts with a recorded violation." in html
    assert 'data-conflict="true"' in html
    assert '<option value="Supports" selected>Supports</option>' in html
    assert "DRAFT · HUMAN REVIEW REQUIRED" in html


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "data:text/html,unsafe",
        "//example.test",
        "file:///tmp/a",
        "https://bad.test/\nunsafe",
        "https:\\example.test",
        "https://[bad-host",
        "https:///missing-host",
        "https://user:secret@example.test/",
    ],
)
def test_unsafe_urls_are_text_only(value):
    assert _safe_url(value) == ""


def test_html_disguised_as_screenshot_is_not_copied(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "fake.png").write_text("<script>alert(1)</script>", encoding="utf-8")
    document = draft(
        [
            Evidence(
                evidence_id="fake",
                state_id="state-1",
                kind="screenshot",
                path="fake.png",
            )
        ]
    )
    output = tmp_path / "output"
    html = write_acr(document, output, source).read_text(encoding="utf-8")
    assert "supported image signature" in html
    assert not (output / "evidence").exists()


def test_comparison_renders_optional_metadata_changes_and_safe_empty_defaults(tmp_path):
    minimal = write_comparison({}, tmp_path).read_text(encoding="utf-8")
    assert "No finding changes match" in minimal
    assert "Not recorded" in minimal
    comparison = {
        "baseline_scan_id": "before",
        "current_scan_id": "after",
        "counts": {"not_verified": 1},
        "warnings": ["Coverage <incomplete>"],
        "products": {
            "baseline": {"name": "Example", "version": "1.0"},
            "current": {"name": "Example", "version": "2.0"},
        },
        "baseline_coverage": {"states_scanned": 2},
        "current_coverage": {"states_scanned": 1},
        "items": [
            {
                "status": "not_verified",
                "title": '</script><script>alert("unsafe")</script>',
                "url": "javascript:alert(1)",
                "target": ["<img>"],
                "baseline_occurrences": 1,
                "current_occurrences": 0,
                "reason": "Page was not retested",
            }
        ],
        "criterion_changes": [
            {
                "criterion_id": "1.1.1",
                "title": "Non-text Content",
                "baseline_conformance": None,
                "current_conformance": "Supports",
                "status": "not_verified",
            }
        ],
    }
    html = write_comparison(comparison, tmp_path).read_text(encoding="utf-8")
    assert "Example · 1.0" in html and "Example · 2.0" in html
    assert "Coverage &lt;incomplete&gt;" in html
    assert "Baseline coverage" in html
    assert "Criterion review changes" in html
    assert "Pending review" in html
    assert "Page was not retested" in html
    assert '<script>alert("unsafe")</script>' not in html
    assert 'href="javascript:' not in html
    assert json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8")) == comparison
