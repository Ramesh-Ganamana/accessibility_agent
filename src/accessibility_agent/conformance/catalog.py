"""Versioned WCAG success-criterion metadata, not an automated coverage claim.

Metadata was verified against the W3C Recommendations on 2026-09-09.
See ``docs/wcag-catalog-sources.md`` for sources, counts, and limitations.
"""

from dataclasses import dataclass, replace
from typing import Literal

WcagVersion = Literal["2.0", "2.1", "2.2"]
ConformanceLevel = Literal["A", "AA", "AAA"]

SUPPORTED_VERSIONS: tuple[WcagVersion, ...] = ("2.0", "2.1", "2.2")
SUPPORTED_LEVELS: tuple[ConformanceLevel, ...] = ("A", "AA", "AAA")


@dataclass(frozen=True, slots=True)
class Criterion:
    """One success criterion with its introduction and removal metadata.

    ``CATALOG`` uses the latest applicable Recommendation's title and URL.
    ``criteria_for`` returns titles and URLs for the requested version.
    ``obsolete_in`` is inclusive: the criterion is absent from that version.
    """

    id: str
    title: str
    level: ConformanceLevel
    introduced: WcagVersion
    obsolete_in: WcagVersion | None
    url: str


# ID, title, level, first version, WCAG 2.2 anchor (2.1 anchor for Parsing).
# These are metadata labels; the normative criterion text remains at W3C.
_RECORDS: tuple[tuple[str, str, ConformanceLevel, WcagVersion, str], ...] = (
    ("1.1.1", "Non-text Content", "A", "2.0", "non-text-content"),
    (
        "1.2.1",
        "Audio-only and Video-only (Prerecorded)",
        "A",
        "2.0",
        "audio-only-and-video-only-prerecorded",
    ),
    ("1.2.2", "Captions (Prerecorded)", "A", "2.0", "captions-prerecorded"),
    (
        "1.2.3",
        "Audio Description or Media Alternative (Prerecorded)",
        "A",
        "2.0",
        "audio-description-or-media-alternative-prerecorded",
    ),
    ("1.2.4", "Captions (Live)", "AA", "2.0", "captions-live"),
    ("1.2.5", "Audio Description (Prerecorded)", "AA", "2.0", "audio-description-prerecorded"),
    ("1.2.6", "Sign Language (Prerecorded)", "AAA", "2.0", "sign-language-prerecorded"),
    (
        "1.2.7",
        "Extended Audio Description (Prerecorded)",
        "AAA",
        "2.0",
        "extended-audio-description-prerecorded",
    ),
    ("1.2.8", "Media Alternative (Prerecorded)", "AAA", "2.0", "media-alternative-prerecorded"),
    ("1.2.9", "Audio-only (Live)", "AAA", "2.0", "audio-only-live"),
    ("1.3.1", "Info and Relationships", "A", "2.0", "info-and-relationships"),
    ("1.3.2", "Meaningful Sequence", "A", "2.0", "meaningful-sequence"),
    ("1.3.3", "Sensory Characteristics", "A", "2.0", "sensory-characteristics"),
    ("1.3.4", "Orientation", "AA", "2.1", "orientation"),
    ("1.3.5", "Identify Input Purpose", "AA", "2.1", "identify-input-purpose"),
    ("1.3.6", "Identify Purpose", "AAA", "2.1", "identify-purpose"),
    ("1.4.1", "Use of Color", "A", "2.0", "use-of-color"),
    ("1.4.2", "Audio Control", "A", "2.0", "audio-control"),
    ("1.4.3", "Contrast (Minimum)", "AA", "2.0", "contrast-minimum"),
    ("1.4.4", "Resize Text", "AA", "2.0", "resize-text"),
    ("1.4.5", "Images of Text", "AA", "2.0", "images-of-text"),
    ("1.4.6", "Contrast (Enhanced)", "AAA", "2.0", "contrast-enhanced"),
    ("1.4.7", "Low or No Background Audio", "AAA", "2.0", "low-or-no-background-audio"),
    ("1.4.8", "Visual Presentation", "AAA", "2.0", "visual-presentation"),
    ("1.4.9", "Images of Text (No Exception)", "AAA", "2.0", "images-of-text-no-exception"),
    ("1.4.10", "Reflow", "AA", "2.1", "reflow"),
    ("1.4.11", "Non-text Contrast", "AA", "2.1", "non-text-contrast"),
    ("1.4.12", "Text Spacing", "AA", "2.1", "text-spacing"),
    ("1.4.13", "Content on Hover or Focus", "AA", "2.1", "content-on-hover-or-focus"),
    ("2.1.1", "Keyboard", "A", "2.0", "keyboard"),
    ("2.1.2", "No Keyboard Trap", "A", "2.0", "no-keyboard-trap"),
    ("2.1.3", "Keyboard (No Exception)", "AAA", "2.0", "keyboard-no-exception"),
    ("2.1.4", "Character Key Shortcuts", "A", "2.1", "character-key-shortcuts"),
    ("2.2.1", "Timing Adjustable", "A", "2.0", "timing-adjustable"),
    ("2.2.2", "Pause, Stop, Hide", "A", "2.0", "pause-stop-hide"),
    ("2.2.3", "No Timing", "AAA", "2.0", "no-timing"),
    ("2.2.4", "Interruptions", "AAA", "2.0", "interruptions"),
    ("2.2.5", "Re-authenticating", "AAA", "2.0", "re-authenticating"),
    ("2.2.6", "Timeouts", "AAA", "2.1", "timeouts"),
    ("2.3.1", "Three Flashes or Below Threshold", "A", "2.0", "three-flashes-or-below-threshold"),
    ("2.3.2", "Three Flashes", "AAA", "2.0", "three-flashes"),
    ("2.3.3", "Animation from Interactions", "AAA", "2.1", "animation-from-interactions"),
    ("2.4.1", "Bypass Blocks", "A", "2.0", "bypass-blocks"),
    ("2.4.2", "Page Titled", "A", "2.0", "page-titled"),
    ("2.4.3", "Focus Order", "A", "2.0", "focus-order"),
    ("2.4.4", "Link Purpose (In Context)", "A", "2.0", "link-purpose-in-context"),
    ("2.4.5", "Multiple Ways", "AA", "2.0", "multiple-ways"),
    ("2.4.6", "Headings and Labels", "AA", "2.0", "headings-and-labels"),
    ("2.4.7", "Focus Visible", "AA", "2.0", "focus-visible"),
    ("2.4.8", "Location", "AAA", "2.0", "location"),
    ("2.4.9", "Link Purpose (Link Only)", "AAA", "2.0", "link-purpose-link-only"),
    ("2.4.10", "Section Headings", "AAA", "2.0", "section-headings"),
    ("2.4.11", "Focus Not Obscured (Minimum)", "AA", "2.2", "focus-not-obscured-minimum"),
    ("2.4.12", "Focus Not Obscured (Enhanced)", "AAA", "2.2", "focus-not-obscured-enhanced"),
    ("2.4.13", "Focus Appearance", "AAA", "2.2", "focus-appearance"),
    ("2.5.1", "Pointer Gestures", "A", "2.1", "pointer-gestures"),
    ("2.5.2", "Pointer Cancellation", "A", "2.1", "pointer-cancellation"),
    ("2.5.3", "Label in Name", "A", "2.1", "label-in-name"),
    ("2.5.4", "Motion Actuation", "A", "2.1", "motion-actuation"),
    ("2.5.5", "Target Size (Enhanced)", "AAA", "2.1", "target-size-enhanced"),
    ("2.5.6", "Concurrent Input Mechanisms", "AAA", "2.1", "concurrent-input-mechanisms"),
    ("2.5.7", "Dragging Movements", "AA", "2.2", "dragging-movements"),
    ("2.5.8", "Target Size (Minimum)", "AA", "2.2", "target-size-minimum"),
    ("3.1.1", "Language of Page", "A", "2.0", "language-of-page"),
    ("3.1.2", "Language of Parts", "AA", "2.0", "language-of-parts"),
    ("3.1.3", "Unusual Words", "AAA", "2.0", "unusual-words"),
    ("3.1.4", "Abbreviations", "AAA", "2.0", "abbreviations"),
    ("3.1.5", "Reading Level", "AAA", "2.0", "reading-level"),
    ("3.1.6", "Pronunciation", "AAA", "2.0", "pronunciation"),
    ("3.2.1", "On Focus", "A", "2.0", "on-focus"),
    ("3.2.2", "On Input", "A", "2.0", "on-input"),
    ("3.2.3", "Consistent Navigation", "AA", "2.0", "consistent-navigation"),
    ("3.2.4", "Consistent Identification", "AA", "2.0", "consistent-identification"),
    ("3.2.5", "Change on Request", "AAA", "2.0", "change-on-request"),
    ("3.2.6", "Consistent Help", "A", "2.2", "consistent-help"),
    ("3.3.1", "Error Identification", "A", "2.0", "error-identification"),
    ("3.3.2", "Labels or Instructions", "A", "2.0", "labels-or-instructions"),
    ("3.3.3", "Error Suggestion", "AA", "2.0", "error-suggestion"),
    (
        "3.3.4",
        "Error Prevention (Legal, Financial, Data)",
        "AA",
        "2.0",
        "error-prevention-legal-financial-data",
    ),
    ("3.3.5", "Help", "AAA", "2.0", "help"),
    ("3.3.6", "Error Prevention (All)", "AAA", "2.0", "error-prevention-all"),
    ("3.3.7", "Redundant Entry", "A", "2.2", "redundant-entry"),
    (
        "3.3.8",
        "Accessible Authentication (Minimum)",
        "AA",
        "2.2",
        "accessible-authentication-minimum",
    ),
    (
        "3.3.9",
        "Accessible Authentication (Enhanced)",
        "AAA",
        "2.2",
        "accessible-authentication-enhanced",
    ),
    ("4.1.1", "Parsing", "A", "2.0", "parsing"),
    ("4.1.2", "Name, Role, Value", "A", "2.0", "name-role-value"),
    ("4.1.3", "Status Messages", "AA", "2.1", "status-messages"),
)

CATALOG: tuple[Criterion, ...] = tuple(
    Criterion(
        id=identifier,
        title=title,
        level=level,
        introduced=introduced,
        obsolete_in="2.2" if identifier == "4.1.1" else None,
        url=f"https://www.w3.org/TR/WCAG{'21' if identifier == '4.1.1' else '22'}/#{anchor}",
    )
    for identifier, title, level, introduced, anchor in _RECORDS
)

# WCAG 2.0 predates the descriptive anchors used by WCAG 2.1 and 2.2.
_WCAG20_ANCHORS: dict[str, str] = {
    "1.1.1": "text-equiv-all",
    "1.2.1": "media-equiv-av-only-alt",
    "1.2.2": "media-equiv-captions",
    "1.2.3": "media-equiv-audio-desc",
    "1.2.4": "media-equiv-real-time-captions",
    "1.2.5": "media-equiv-audio-desc-only",
    "1.2.6": "media-equiv-sign",
    "1.2.7": "media-equiv-extended-ad",
    "1.2.8": "media-equiv-text-doc",
    "1.2.9": "media-equiv-live-audio-only",
    "1.3.1": "content-structure-separation-programmatic",
    "1.3.2": "content-structure-separation-sequence",
    "1.3.3": "content-structure-separation-understanding",
    "1.4.1": "visual-audio-contrast-without-color",
    "1.4.2": "visual-audio-contrast-dis-audio",
    "1.4.3": "visual-audio-contrast-contrast",
    "1.4.4": "visual-audio-contrast-scale",
    "1.4.5": "visual-audio-contrast-text-presentation",
    "1.4.6": "visual-audio-contrast7",
    "1.4.7": "visual-audio-contrast-noaudio",
    "1.4.8": "visual-audio-contrast-visual-presentation",
    "1.4.9": "visual-audio-contrast-text-images",
    "2.1.1": "keyboard-operation-keyboard-operable",
    "2.1.2": "keyboard-operation-trapping",
    "2.1.3": "keyboard-operation-all-funcs",
    "2.2.1": "time-limits-required-behaviors",
    "2.2.2": "time-limits-pause",
    "2.2.3": "time-limits-no-exceptions",
    "2.2.4": "time-limits-postponed",
    "2.2.5": "time-limits-server-timeout",
    "2.3.1": "seizure-does-not-violate",
    "2.3.2": "seizure-three-times",
    "2.4.1": "navigation-mechanisms-skip",
    "2.4.2": "navigation-mechanisms-title",
    "2.4.3": "navigation-mechanisms-focus-order",
    "2.4.4": "navigation-mechanisms-refs",
    "2.4.5": "navigation-mechanisms-mult-loc",
    "2.4.6": "navigation-mechanisms-descriptive",
    "2.4.7": "navigation-mechanisms-focus-visible",
    "2.4.8": "navigation-mechanisms-location",
    "2.4.9": "navigation-mechanisms-link",
    "2.4.10": "navigation-mechanisms-headings",
    "3.1.1": "meaning-doc-lang-id",
    "3.1.2": "meaning-other-lang-id",
    "3.1.3": "meaning-idioms",
    "3.1.4": "meaning-located",
    "3.1.5": "meaning-supplements",
    "3.1.6": "meaning-pronunciation",
    "3.2.1": "consistent-behavior-receive-focus",
    "3.2.2": "consistent-behavior-unpredictable-change",
    "3.2.3": "consistent-behavior-consistent-locations",
    "3.2.4": "consistent-behavior-consistent-functionality",
    "3.2.5": "consistent-behavior-no-extreme-changes-context",
    "3.3.1": "minimize-error-identified",
    "3.3.2": "minimize-error-cues",
    "3.3.3": "minimize-error-suggestions",
    "3.3.4": "minimize-error-reversible",
    "3.3.5": "minimize-error-context-help",
    "3.3.6": "minimize-error-reversible-all",
    "4.1.1": "ensure-compat-parses",
    "4.1.2": "ensure-compat-rsv",
}


def criteria_for(version: str = "2.2", level: str = "AA") -> tuple[Criterion, ...]:
    """Return active criteria up to ``level`` in numeric criterion order.

    AA includes A and AA; AAA includes all three levels. Unsupported versions
    or levels raise ``ValueError`` instead of silently returning an empty set.
    A selected criterion means review is required, not that a scanner tests it.
    """
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"Unsupported WCAG version {version!r}; choose 2.0, 2.1, or 2.2")
    if level not in SUPPORTED_LEVELS:
        raise ValueError(f"Unsupported WCAG level {level!r}; choose A, AA, or AAA")
    versions: tuple[str, ...] = SUPPORTED_VERSIONS
    levels: tuple[str, ...] = SUPPORTED_LEVELS
    selected: list[Criterion] = []
    for criterion in CATALOG:
        if versions.index(criterion.introduced) > versions.index(version):
            continue
        if criterion.obsolete_in and (
            versions.index(version) >= versions.index(criterion.obsolete_in)
        ):
            continue
        if levels.index(criterion.level) > levels.index(level):
            continue
        anchor = criterion.url.partition("#")[2]
        title = criterion.title
        if version == "2.0":
            anchor = _WCAG20_ANCHORS[criterion.id]
            if criterion.id == "1.4.4":
                title = "Resize text"
        elif version == "2.1" and criterion.id == "2.5.5":
            title, anchor = "Target Size", "target-size"
        selected.append(
            replace(
                criterion,
                title=title,
                url=f"https://www.w3.org/TR/WCAG{version.replace('.', '')}/#{anchor}",
            )
        )
    return tuple(selected)


# Original, non-normative review prompts grouped by WCAG guideline. These are
# starting points for an evaluator, not a complete test procedure or result.
_MANUAL_GUIDANCE: dict[str, str] = {
    "1.1": "Review whether text alternatives communicate the purpose of each non-text item.",
    "1.2": "Review media, alternatives, captions, and descriptions for accuracy and timing.",
    "1.3": "Review structure, reading order, orientation, and programmatic meaning in context.",
    "1.4": "Review visual and audio presentation, zoom, spacing, and applicable contrast measures.",
    "2.1": "Operate the relevant controls and complete the process using keyboard input.",
    "2.2": "Exercise time limits, moving content, interruptions, and session expiry where present.",
    "2.3": "Use suitable analysis for flashes and review motion controls; avoid unsafe exposure.",
    "2.4": "Review navigation, labels, focus sequence, visibility, and obscuration across states.",
    "2.5": "Exercise relevant pointer, touch, speech, and motion input; measure relevant targets.",
    "3.1": "Review language metadata and the meaning and readability of the actual content.",
    "3.2": "Review context changes and consistency across pages, controls, and help mechanisms.",
    "3.3": "Complete forms and authentication, including errors, recovery, and repeated entry.",
    "4.1": "Inspect programmatic semantics and dynamic updates with relevant assistive technology.",
}


def manual_review_guidance(criterion: Criterion | str) -> tuple[str, ...]:
    """Return non-exhaustive human-review prompts, never an automatic verdict.

    A ``Criterion`` from ``criteria_for`` preserves its version-specific source
    link. A string must be a catalog identifier, for example ``\"1.1.1\"``.
    """
    if isinstance(criterion, str):
        matches = (record for record in CATALOG if record.id == criterion)
        record = next(matches, None)
        if record is None:
            raise ValueError(f"Unknown WCAG criterion {criterion!r}")
        criterion = record
    guideline = criterion.id.rsplit(".", 1)[0]
    if guideline not in _MANUAL_GUIDANCE:
        raise ValueError(f"Unknown WCAG guideline for criterion {criterion.id!r}")
    return (
        f"Read {criterion.id} {criterion.title}, including applicability and exceptions: "
        f"{criterion.url}",
        _MANUAL_GUIDANCE[guideline],
        "Record the reviewed pages, states, complete processes, tools, observed results, "
        "and evidence. Document the reason for any Not Applicable decision.",
        "These prompts are not a complete test procedure. Automated results alone do not "
        "establish conformance; a qualified reviewer must assess the criterion and scope.",
    )
