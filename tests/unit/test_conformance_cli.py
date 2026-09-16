import json

from test_conformance import decision, sample_report

from accessibility_agent.conformance.models import DraftACR, ReviewSet
from accessibility_agent.main import main


def prepare(tmp_path, outcome="violation"):
    source = tmp_path / "source.json"
    source.write_text(sample_report(outcome=outcome).model_dump_json(), encoding="utf-8")
    output = tmp_path / "draft"
    args = [
        "acr", "prepare", "--report", str(source), "--application-id", "demo-qa",
        "--product-name", "Demo", "--product-version", "1.0", "--scope", "Dashboard",
        "--output", str(output),
    ]
    assert main(args) == 0
    return source, output, args


def test_cli_prepare_build_compare_preserves_source_and_review(tmp_path):
    source, output, _ = prepare(tmp_path)
    original = source.read_bytes()
    review_path = output / "review.json"
    review = ReviewSet.model_validate_json(review_path.read_text(encoding="utf-8"))
    assert len(review.criteria) == 55
    review.criteria = [decision() if row.criterion_id == "4.1.2" else row
                       for row in review.criteria]
    review_path.write_text(review.model_dump_json(), encoding="utf-8")
    edited = review_path.read_bytes()
    assert main([
        "acr", "build", "--report", str(source), "--review", str(review_path),
        "--output", str(output),
    ]) == 0
    document = DraftACR.model_validate_json((output / "acr.json").read_text(encoding="utf-8"))
    assert document.pending_count == 54
    comparison_dir = tmp_path / "comparison"
    assert main([
        "acr", "compare", "--baseline", str(source), "--current", str(source),
        "--baseline-review", str(review_path), "--current-review", str(review_path),
        "--output", str(comparison_dir),
    ]) == 0
    comparison = json.loads((comparison_dir / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["counts"]["still_present"] == 1
    assert len(comparison["criterion_changes"]) == 55
    assert comparison["products"]["current"]["version"] == "1.0"
    assert source.read_bytes() == original
    assert review_path.read_bytes() == edited


def test_prepare_cannot_overwrite_existing_review(tmp_path):
    _, output, args = prepare(tmp_path)
    original = (output / "review.json").read_bytes()
    assert main(args) == 2
    assert (output / "review.json").read_bytes() == original


def test_build_rejects_changed_scan_and_does_not_publish(tmp_path):
    source, output, _ = prepare(tmp_path)
    report = sample_report()
    report.scan.scan_id = "new-release"
    source.write_text(report.model_dump_json(), encoding="utf-8")
    target = tmp_path / "invalid-output"
    assert main([
        "acr", "build", "--report", str(source), "--review", str(output / "review.json"),
        "--output", str(target),
    ]) == 2
    assert not target.exists()


def test_cli_conflict_is_reported_without_omitting_evidence(tmp_path):
    source, output, _ = prepare(tmp_path)
    review_path = output / "review.json"
    review = ReviewSet.model_validate_json(review_path.read_text(encoding="utf-8"))
    review.criteria = [decision(value="Supports") if row.criterion_id == "4.1.2" else row
                       for row in review.criteria]
    review_path.write_text(review.model_dump_json(), encoding="utf-8")
    assert main([
        "acr", "build", "--report", str(source), "--review", str(review_path),
        "--output", str(output),
    ]) == 3
    document = DraftACR.model_validate_json((output / "acr.json").read_text(encoding="utf-8"))
    assert document.conflict_count == 1


def test_compare_requires_both_review_inputs(tmp_path):
    source, output, _ = prepare(tmp_path)
    assert main([
        "acr", "compare", "--baseline", str(source), "--current", str(source),
        "--baseline-review", str(output / "review.json"), "--output", str(tmp_path / "compare"),
    ]) == 2


def test_cli_rejects_invalid_input_without_exposing_content(tmp_path, capsys):
    source = tmp_path / "source.json"
    source.write_text('{"private":"DO-NOT-PRINT-THIS"}', encoding="utf-8")
    assert main([
        "acr", "prepare", "--report", str(source), "--application-id", "demo",
        "--product-name", "Demo", "--product-version", "1.0", "--scope", "Home",
        "--output", str(tmp_path / "output"),
    ]) == 2
    assert "DO-NOT-PRINT-THIS" not in capsys.readouterr().out
