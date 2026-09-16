"""Catalog integrity and version boundaries verified against W3C metadata."""

from collections import Counter
from dataclasses import FrozenInstanceError
from urllib.parse import urlsplit

import pytest

from accessibility_agent.conformance.catalog import (
    CATALOG,
    SUPPORTED_LEVELS,
    SUPPORTED_VERSIONS,
    criteria_for,
    manual_review_guidance,
)


@pytest.mark.parametrize(
    ("version", "level", "count"),
    [
        ("2.0", "A", 25),
        ("2.0", "AA", 38),
        ("2.0", "AAA", 61),
        ("2.1", "A", 30),
        ("2.1", "AA", 50),
        ("2.1", "AAA", 78),
        ("2.2", "A", 31),
        ("2.2", "AA", 55),
        ("2.2", "AAA", 86),
    ],
)
def test_w3c_criterion_counts(version: str, level: str, count: int) -> None:
    assert len(criteria_for(version, level)) == count


def test_catalog_has_unique_ordered_immutable_records() -> None:
    ids = [criterion.id for criterion in CATALOG]
    assert len(ids) == len(set(ids)) == 87
    assert ids == sorted(ids, key=lambda identifier: tuple(map(int, identifier.split("."))))
    with pytest.raises(FrozenInstanceError):
        CATALOG[0].title = "Changed"  # type: ignore[misc]


def test_wcag_21_additions_match_w3c_new_features() -> None:
    old = {criterion.id for criterion in criteria_for("2.0", "AAA")}
    new = {criterion.id for criterion in criteria_for("2.1", "AAA")}
    assert new - old == {
        "1.3.4",
        "1.3.5",
        "1.3.6",
        "1.4.10",
        "1.4.11",
        "1.4.12",
        "1.4.13",
        "2.1.4",
        "2.2.6",
        "2.3.3",
        "2.5.1",
        "2.5.2",
        "2.5.3",
        "2.5.4",
        "2.5.5",
        "2.5.6",
        "4.1.3",
    }
    assert old <= new


def test_wcag_22_additions_and_parsing_removal_match_w3c() -> None:
    old = {criterion.id for criterion in criteria_for("2.1", "AAA")}
    new = {criterion.id for criterion in criteria_for("2.2", "AAA")}
    assert new - old == {
        "2.4.11",
        "2.4.12",
        "2.4.13",
        "2.5.7",
        "2.5.8",
        "3.2.6",
        "3.3.7",
        "3.3.8",
        "3.3.9",
    }
    assert old - new == {"4.1.1"}
    parsing = next(criterion for criterion in CATALOG if criterion.id == "4.1.1")
    assert (parsing.level, parsing.introduced, parsing.obsolete_in) == ("A", "2.0", "2.2")


def test_default_includes_a_and_aa_only() -> None:
    assert criteria_for() == criteria_for("2.2", "AA")
    assert Counter(criterion.level for criterion in criteria_for()) == {"A": 31, "AA": 24}
    assert Counter(criterion.introduced for criterion in CATALOG) == {
        "2.0": 61,
        "2.1": 17,
        "2.2": 9,
    }


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_urls_reference_requested_official_recommendation(version: str) -> None:
    for criterion in criteria_for(version, "AAA"):
        parsed = urlsplit(criterion.url)
        assert (parsed.scheme, parsed.netloc) == ("https", "www.w3.org")
        assert parsed.path == f"/TR/WCAG{version.replace('.', '')}/"
        assert parsed.fragment


def test_version_specific_titles_and_legacy_anchors() -> None:
    wcag20 = {criterion.id: criterion for criterion in criteria_for("2.0", "AAA")}
    wcag21 = {criterion.id: criterion for criterion in criteria_for("2.1", "AAA")}
    wcag22 = {criterion.id: criterion for criterion in criteria_for("2.2", "AAA")}
    assert wcag20["1.1.1"].url.endswith("#text-equiv-all")
    assert wcag20["4.1.1"].url.endswith("#ensure-compat-parses")
    assert wcag20["1.4.4"].title == "Resize text"
    assert wcag21["2.5.5"].title == "Target Size"
    assert wcag21["2.5.5"].url.endswith("#target-size")
    assert wcag22["2.5.5"].title == "Target Size (Enhanced)"
    assert wcag22["2.5.5"].url.endswith("#target-size-enhanced")


@pytest.mark.parametrize("version", ["", "1.0", "2", "2.3", "3.0"])
def test_unknown_version_is_rejected(version: str) -> None:
    with pytest.raises(ValueError, match="Unsupported WCAG version"):
        criteria_for(version)


@pytest.mark.parametrize("level", ["", "a", "aa", "AAAA", "all"])
def test_unknown_level_is_rejected(level: str) -> None:
    with pytest.raises(ValueError, match="Unsupported WCAG level"):
        criteria_for(level=level)


def test_manual_prompts_cover_every_selected_criterion_without_claiming_conformance() -> None:
    for version in SUPPORTED_VERSIONS:
        for level in SUPPORTED_LEVELS:
            for criterion in criteria_for(version, level):
                guidance = manual_review_guidance(criterion)
                assert criterion.url in guidance[0]
                assert "Automated results alone do not establish conformance" in guidance[-1]
    assert "keyboard" in manual_review_guidance("2.1.1")[1]


def test_manual_prompts_reject_unknown_identifier() -> None:
    with pytest.raises(ValueError, match="Unknown WCAG criterion"):
        manual_review_guidance("9.9.9")
