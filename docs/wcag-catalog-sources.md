# WCAG catalog provenance and boundaries

The criterion metadata in `src/accessibility_agent/conformance/catalog.py` was
verified on 9 September 2026 against these primary sources:

- [W3C WCAG 2.0 Recommendation](https://www.w3.org/TR/WCAG20/)
- [W3C WCAG 2.1 Recommendation](https://www.w3.org/TR/WCAG21/)
- [W3C WCAG 2.2 Recommendation](https://www.w3.org/TR/WCAG22/)

Verification read criterion identifiers, titles, conformance levels, and fragment
anchors from each Recommendation. Introductions were cross-checked against
[new features in WCAG 2.1](https://www.w3.org/TR/WCAG21/#new-features-in-wcag-2-1)
and [new features in WCAG 2.2](https://www.w3.org/TR/WCAG22/#new-features-in-wcag-2-2).
The catalog contains metadata and links, not a reproduction of normative criteria.

## Verified counts

Counts below are calculated from each Recommendation's criterion listings. Exact
levels count only that level; cumulative levels include the preceding levels.

| Version | A only | AA only | AAA only | A cumulative | AA cumulative | AAA cumulative |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2.0 | 25 | 13 | 23 | 25 | 38 | 61 |
| 2.1 | 30 | 20 | 28 | 30 | 50 | 78 |
| 2.2 | 31 | 24 | 31 | 31 | 55 | 86 |

WCAG 2.1 adds 17 criteria. WCAG 2.2 adds nine and removes 4.1.1 Parsing, giving
86 active criteria. The full historic catalog therefore contains 87 records.
Parsing remains selectable for 2.0 and 2.1; its `obsolete_in` value is `2.2`.
W3C explains the removal and implications for reporting earlier versions in its
[comparison with WCAG 2.1](https://www.w3.org/TR/WCAG22/#comparison-with-wcag-2-1).
The current 2.1 Recommendation also provides an explanatory note at
[4.1.1 Parsing](https://www.w3.org/TR/WCAG21/#parsing); selection of this row does
not imply that any particular parsing rule still demonstrates a user barrier.

## API behavior

`CATALOG` is an immutable tuple of frozen `Criterion` records in numeric order.
Each record provides `id`, `title`, `level`, `introduced`, `obsolete_in`, and `url`.
Catalog records use the latest applicable Recommendation's title and URL.

`criteria_for(version="2.2", level="AA")` returns an immutable tuple for the
requested version, including all levels up to the requested level. It accepts
versions `2.0`, `2.1`, and `2.2`, and levels `A`, `AA`, and `AAA`; unsupported
values raise `ValueError`. It provides URLs with the actual fragment anchors
from the requested Recommendation, including WCAG 2.0's older anchor scheme.
For 2.1, criterion 2.5.5 is titled “Target Size”; for 2.2, it is titled
“Target Size (Enhanced)”. Both refer to level AAA.
WCAG 2.0's title capitalization for 1.4.4 (“Resize text”) is preserved;
extra whitespace in published titles is normalized to a single space.

## Human evaluation is required

The catalog expresses which criteria belong to a chosen standard and level.
It does not assert that the scanner evaluates each criterion. Passing an
automated rule, or having no findings mapped to a criterion, is insufficient to
mark that criterion as supported. Criteria without adequate evidence require
review; Not Applicable requires a documented applicability decision.

`manual_review_guidance(criterion)` provides original, non-normative prompts by
guideline plus a link to the exact criterion. These are starting points, not
exhaustive test procedures or authoritative interpretations. A qualified reviewer
must assess applicability, exceptions, the relevant pages and states, complete
processes, and the evidence before making a conformance statement. W3C's
[conformance requirements](https://www.w3.org/TR/WCAG22/#conformance-reqs)
include requirements beyond the success-criterion checklist. Neither this
catalog nor a generated report constitutes independent certification.

The metadata is bundled and does not fetch external content during scans or
tests. Updating it requires checking the official Recommendations again and
updating the version-boundary and count tests.
