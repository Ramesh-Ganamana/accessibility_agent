# Accessibility scanning

axe-core 4.10.3 is bundled locally and checksum-verified before scans. Its source
URL/version/SHA-256 are in accessibility/vendor/manifest.json, alongside the
unmodified MPL-2.0 licensed bundle and upstream license. No runtime CDN is used.

Cumulative WCAG version/level tags select rules. Best-practice rules are not added
implicitly. wcag111 maps to 1.1.1 and wcag2411 to 2.4.11. The bundle determines the
available checks; selecting a standard does not automate every criterion.

ScanResult retains violations/incomplete/passes/inapplicable, engine version,
HTML/targets, help, tags and original impact. Incomplete findings are NEEDS_REVIEW.
Groups use source/rule/classification/selector and retain per-state occurrences.
Similar selectors do not prove components have the same underlying implementation.

Every successfully captured screenshot links to its state's findings. Inventories
strip scripts/values and arbitrary attributes. Screenshots mask inputs, private
regions and known credentials, but cannot guarantee all sensitive data is hidden.
No cookies, storage or headers are exported. No AI calls are made in Phase 2.

HTML is autoescaped with a restrictive CSP and no external assets. JSON includes
all engine outcomes; graph.json includes actions. PDF remains a future ReportWriter.
Frames are excluded from axe and reported. Keyboard, AX tree, validation workflows,
announcement analysis and AI remain future. Conformance needs manual review.

Primary references:
- [axe-core API](https://github.com/dequelabs/axe-core/blob/v4.10.3/doc/API.md)
- [Playwright Python](https://playwright.dev/python/docs/library)

Phase 2 now invokes the same engine for each distinct interactive state, with
state screenshots and occurrence-level references. A repeated URL can contain
multiple scans. No new heuristic accessibility checks are presented as axe results.
