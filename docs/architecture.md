# Architecture and phase boundaries

Status: Phase 2 implemented. Interactive state discovery, verified replay and
same-URL state deduplication extend the existing browser/auth/axe/evidence/report
pipeline. The Phase 1 link-only mode remains available. Keyboard, AX, AI and
advanced dashboard work below remain a roadmap for later phases.

## Boundaries

CLI -> validated Settings -> Orchestrator -> BrowserSession -> Authenticator
-> StateCrawler -> AccessibilityScanner -> EvidenceCollector -> FindingRepository
-> optional AIProvider -> ReportWriter.

Each boundary is an asynchronous Python Protocol. The orchestrator owns browser
lifetime, deadlines, cancellation, and per-state failure isolation. Implementations
will be injected, keeping browser, scanning, AI, and rendering independent.
JSON and HTML implement the same ReportWriter contract; PDF can follow later.
No empty implementation classes pretend to perform scans.

## Contracts defined before implementation

Settings: application, authentication, browser, crawl, accessibility, keyboard,
visual, AI, and output. Credentials are excluded from serialization and repr.
State: ID, URL, title, DOM/AX hashes, structured visible and interactive elements,
dialogs, menus, tabs, forms, evidence references, depth, and scan status.
Action: ID, source/target states, element, type, safety category, outcome, reason.
Finding: original rule impact, separate business severity, WCAG criteria,
principles, source, confidence, classification, explanation and occurrences.
Report: schema version, application, scan metadata, configuration, coverage,
summary, findings, keyboard results, graph, evidence and unscanned areas.

## Authentication design (Phase 1)

Identify visible editable username/email and password fields using input type,
autocomplete, labels, accessible names and placeholders; rank likely submit
controls inside the same form. Selector overrides take precedence. Permit a
bounded username-first flow. Confirm success from a configured success selector
or combined disappearance of login controls and authenticated navigation;
ambiguous success is reported, never assumed. CAPTCHA, MFA, SSO origin changes,
invalid credentials and permission failures are explicit blocked outcomes.
Keep browser session storage in memory; never persist it by default.

## State crawl design

Bounded breadth-first frontier stores replay paths from an authenticated entry
state. Restore a baseline and replay validated actions before expanding siblings;
verify each intermediate fingerprint, recording replay divergence instead of
mislabeling states. Fingerprint normalized URL (retain meaningful SPA fragments),
stable DOM structure, visible text, AX semantics and expanded/selected/modal state.
Strip only configured volatile attributes/text; version the fingerprint algorithm.
Deduplicate scans by fingerprint but retain every transition and occurrence.

Discover semantic links, controls, forms, menus, tabs, dialogs and expanders,
including supported shadow roots/frames. Record unsupported surfaces explicitly.
Policy classifies SAFE, CAUTION, DESTRUCTIVE, EXTERNAL and UNKNOWN. Only SAFE is
automatic; exact action allowances may override caution/destructive decisions.
Never submit forms or activate unknown controls blindly. Check actual navigation
and redirect origins as well as hrefs. Depth/state/action/time budgets leave
frontier entries as unscanned, with reasons. Exceptions affect one state where
possible; unrecoverable session failures finalize a partial report.

## Accessibility and evidence design

Phase 1 integrates a version-pinned, locally supplied axe-core JavaScript bundle
with provenance/license/checksum. No runtime CDN injection. Record engine version,
violations, incomplete, passes and inapplicable checks, including node targets,
HTML and tags. WCAG tag mapping is versioned; not every axe rule is a WCAG failure.
Incomplete and heuristic results are NEEDS_REVIEW, not confirmed violations.

Phase 3 adds bounded keyboard probes (Tab, Shift+Tab, Enter, Space, Escape, arrows,
Home/End), focus order/visibility, modal entry/escape/return and dynamic live-region
checks. Activation obeys action policy. Repeated focus alone is not proof of a
trap; require an unreachable exit and report uncertain results as review items.
Capture Chromium AX through an adapter; other engines can return unsupported.
Screenshot/DOM/AX evidence has relative paths and state/occurrence references.

## Security and AI trust boundary

Credentials only live in authentication configuration and browser operations.
Exclude them from model dumps, reports, log events and exception diagnostics.
CLI validation emits field names and error codes, never rejected input values.
URLs with user-info are rejected; report URLs/query parameters need sanitization
at the evidence boundary. Page DOM, AX text, URLs and screenshots may contain
secrets or personal information: redact/allowlist before storage or AI transfer.
Raw browser objects, cookies, storage and headers never enter AI contracts.
The SanitizedAIContext type is a boundary contract, not a sanitizer implementation.
Phase 4 must test redaction before enabling any provider. Treat page text as
untrusted data, never instructions. AI cannot navigate or alter deterministic
results. Preserve impact; suggestions carry AI_INFERENCE and confidence.

## Coverage and reporting

Coverage denominators are the observed discovery frontier, not the whole website.
Track discovered/scanned URLs and states, discovered/tested interactive elements,
keyboard elements and executed rule checks. Zero denominators yield null coverage.
Report safety skips, external links, budget exhaustion, navigation errors, missing
test data and unsupported components separately. Findings group by rule/component
signature with full occurrence details; AI may suggest groups but cannot discard
occurrences. An internal health score is optional, explicitly non-certifying.
HTML must autoescape page content; no executable page HTML enters reports.

## Delivery phases

1. Real CLI scan orchestration, Chromium lifecycle, generic authentication, bounded
   initial state crawl, local axe, evidence, occurrence grouping, JSON/HTML output,
   structured secret-safe logs, demo app and browser integration tests.
2. Rich component transitions, robust replay, SPA fingerprints and graph expansion.
3. Keyboard, focus, AX, form validation and dynamic-content analysis.
4. Provider-neutral contextual enrichment, privacy gate and optional OpenAI adapter.
5. Dashboard drill-down, graph visualization, summaries and coverage presentation.

Automated checks cannot establish WCAG conformance; manual assistive-technology
and contextual review remain necessary. Tests verify contracts and real Phase 1 and Phase 2 browser execution.
