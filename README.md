# Accessibility Testing Agent

**Phase 2 is implemented.** Scan links and safe interactive application states
using Chromium and locally bundled axe-core 4.10.3. Menus, tabs, dialogs, native
selects, custom dropdowns, accordions and configured form inputs are explored
through verified replay. Receive HTML, JSON, screenshots and a state graph.

The original Phase 1 crawler remains available with `--crawl-mode links`.

This branch also adds [WCAG review, draft ACR exports and release comparisons](docs/conformance.md).

## Install

Python 3.12+ is required. In PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.browsers'
.\.venv\Scripts\python -m playwright install chromium
```

Keep PLAYWRIGHT_BROWSERS_PATH set in each shell when using this local installation.
Omit it for Playwright's normal user-cache installation. On Linux/macOS use
`.venv/bin/python`; Linux may need `playwright install --with-deps chromium`.

## Launch and scan (no YAML needed)

After installation, double-click `launch-agent.cmd` on Windows, or run:

```powershell
.\.venv\Scripts\python -m accessibility_agent
```

Enter your login URL, username and password when prompted. Password entry is hidden.
Leave username blank for a public page. The browser opens visibly, scanning starts
after each page is ready, and the HTML report opens when finished. Public pages continue
without any login or setup prompt. Each run gets its own folder under reports. No YAML,
environment variables or pipeline setup is needed.
`python -m accessibility_agent scan` also launches these prompts.
Unsupported authentication still produces an explicit failure report.

Visible Chromium windows launch maximized, with `--window-size=1920,1080` by
default. The scan viewport remains 1920×1080 (or your configured viewport);
the maximized outer window uses the monitor's available desktop area.

## Advanced command-line run

```powershell
$env:A11Y_URL = 'https://your-application.example'
$env:A11Y_USERNAME = Read-Host 'Username'
$env:A11Y_PASSWORD = Read-Host 'Password' -MaskInput
$env:AI_ENABLED = 'false'
.\.venv\Scripts\python -m accessibility_agent scan --output reports/my-scan
```

Read-Host -MaskInput requires PowerShell 7.1+. Environment variables can also come
from a CI secret manager. Public pages need no credentials. The installed
`accessibility-agent` command is equivalent to `python -m accessibility_agent`.
`--url`, `--username`, and `--password` are supported; prefer environment variables
to avoid credentials in process arguments and shell history.

```text
accessibility-agent validate-config --config config.example.yaml
accessibility-agent scan --config config.example.yaml --url https://example.com --output reports/run
```

Outputs in the chosen directory:

- `accessibility-report.html`: offline escaped dashboard and finding details.
- `results.json`: full report including violations, incomplete, passes and inapplicable results.
- `graph.json`: observed states and all recorded navigation actions.
- `screenshots/*.png`: masked state screenshots with numbered, named issue highlights.
- `evidence/*.json`: sanitized visible element inventories.

Use separate directories per run. Report files are atomically replaced; old
evidence is not deleted automatically. Exit codes: **0** completed within the selected
crawl mode, **2** invalid configuration, **3** partial scan, **4** failed scan/report
write, **130** interruption. Findings alone do not cause a nonzero exit code.
Authentication/browser failure still produces a failure report if output is writable.

The offline HTML dashboard includes coverage cards, searchable/filterable/sortable
URL and state tables, interaction outcomes, failures, evidence previews and a bounded
clickable crawl graph. Each accessibility issue shows one representative example
and its total occurrence count; full observations remain in JSON.

## Configuration and authentication

Precedence: defaults < YAML < A11Y_URL/A11Y_USERNAME/A11Y_PASSWORD/AI_ENABLED < CLI.
`--output` overrides YAML output_dir. Unknown fields and invalid bounds fail.
.env.example is reference only; no automatic .env loading occurs.

Login uses input semantics, autocomplete, labels, placeholders, submit names and
one bounded username-first step. YAML selector overrides: authentication's
username_selector, password_selector, submit_selector and success_selector.
Ambiguous login, CAPTCHA/MFA indicators, untrusted cross-origin form actions and
unconfirmed success stop scanning with an explicit reason. Start at the application
or its login page. Standard OAuth/OIDC sign-in redirects are supported as described below;
finding login from a marketing page is not implemented.

Budgets include max_states, max_depth, max_actions_per_state, timeout (per operation
in milliseconds), max_duration_seconds (whole browser run), and settle_ms.
Same-origin navigation is on by default and also checked on redirects. Unsafe,
external and download links are skipped. Exact allowed_action_ids can permit a
caution/destructive link, but never bypass origin policy. IDs appear in graph.json.
Classification is heuristic: use an authorized test environment with test data.

## Interactive crawling and coverage

Interactive crawling is the default (`crawl.mode: interactive`). A fresh browser
context restores authenticated cookies, localStorage, IndexedDB and entry-origin
sessionStorage from an in-memory checkpoint before each branch. Entry and
intermediate fingerprints must match; otherwise the action is reported as
replay_diverged. State fingerprints include URL, DOM/text and selected/expanded/
checked/form state. Multiple states at the same URL are scanned independently;
cycles retain transitions without rescanning identical states.

Safe semantic buttons, menus, tabs, options, summaries, dialog open/close controls
and checkbox/radio controls are exercised. Native selects explore alternative
selections, not the operating system popup. SPA links are clicked to exercise
client handlers. Disabled controls, modal backgrounds, unknown actions, external
links, downloads and submits are skipped by default. Exact allowed_action_ids
may permit an unknown/caution/destructive action once; its destination is scanned
but the action is never replayed and the path is not expanded further.

An additional bounded discovery pass finds custom div/span/card controls with
click handlers, navigation data attributes, or a distinct pointer cursor. They
still pass the existing safety policy; ordinary containers and unknown actions
are not blindly clicked. A maximum of three viewport-height scroll steps per
URL and browser page can reveal lazy controls, then restore the original position.
Unchanged visible state and already-seen fingerprints do not produce another scan
or screenshot.

For an interaction-created window, one safe URL is opened through the existing
guarded navigator and scanned as a state. Original popup requests are stopped
before loading; extra windows are closed. Additional popup destinations, inline
`about:blank` content and unsafe/external destinations are recorded as untested.
This is URL-based popup discovery, not independent multi-window interaction.

### Broader UI exploration on test sites

For authorized QA/staging applications, opt in to unfamiliar clickable controls:

```powershell
.\launch-agent.cmd scan --test-environment
```

This keeps the normal URL/username/password prompts. For a non-interactive run,
add `--test-environment` to your existing `scan` command, or set
`crawl.test_environment: true` in YAML. `--no-test-environment` overrides YAML.
The default remains conservative.

Testing mode permits unfamiliar buttons, role buttons/links and custom click
targets (for example, “My card”) without per-application names. It does not treat
every focusable div as a button. Destructive names, form submissions, disabled or
sensitive controls, origin restrictions and request guards remain in force.
Permitted controls can be replayed; test data may still be affected by arbitrary
click handlers. No automatic server-side rollback is provided.

Interactive discovery also scrolls up to four visible sidebar/content panels,
at most three panel-height steps each, then restores their scroll positions.
This bounded pass runs once per browser page/URL and is not infinite scrolling.
The HTML and JSON reports record the selected testing mode.

Example optional YAML settings (use application-appropriate test data):

```yaml
crawl:
  mode: interactive
  max_states: 100
  max_actions: 300
  max_replay_steps: 20
  max_select_options: 5
  settle_ms: 200
  stability_timeout_ms: 60000
  dom_quiet_ms: 1000
  network_idle_ms: 500
  ready_selector: ""
  loading_selectors: []
  ignore_selectors: [".live-clock"]
  form_values:
    "#search": "sample query"
  allowed_request_urls: []
```

Form input values are opt-in; absent test data is reported. Password/file/hidden
controls are never filled during crawling. Submissions need an action allowance,
and HTTP methods other than GET/HEAD/OPTIONS are blocked in interactive mode
unless their exact URL appears in allowed_request_urls. Any observed HTTP write
makes an action non-replayable, even when explicitly allowed. This cannot roll back
server data or prevent side effects of GET/WebSocket traffic; use a test account
and environment. Non-GET read APIs (such as some GraphQL queries) may need a URL
allowance. Frame and shadow-root interaction remain unsupported and reported.

Budgets cover states, depth, actions per state, total actions, options, replay
length, readiness and total duration. Authentication
that exists only in JavaScript memory or rotating server state may prevent replay.

Both crawl modes wait after navigation and interactive changes for the document
load event, finished requests, fonts and visible images, and no visible loading
indicators. The network must be quiet for `network_idle_ms` (500 ms by default),
followed by unchanged DOM and layout for `dom_quiet_ms` (1000 ms). A blank page or
persistent loader is not counted as scanned. Readiness is bounded by
`stability_timeout_ms` (60000 ms); a timeout records `page_readiness_timeout` in
untested areas, with the signals that were still pending.

For an application with a specific completion signal, set `crawl.ready_selector`
to a selector that becomes visible only when its content is ready. Add custom
spinners or skeletons to `crawl.loading_selectors`; every matching visible loader
must disappear. `crawl.ignore_selectors` can exclude volatile regions from DOM
stability checks. These waits cannot predict future delayed updates; WebSocket
and event-stream connections do not need to close, so application signals are
useful for content delivered through them.

Coverage describes **observed** states/URLs/controls, not the whole application.
Pending URLs never invent state fingerprints. Controls exercised counts actual
original UI actions, not replay repetitions or keyboard tests. Review untested
areas even when the scan completes or reports no violations. axe preserves impact,
and incomplete results remain review items. This is not WCAG certification.

Keyboard accessibility, AX-tree capture, form-validation analysis and AI are later
phases and have not been implemented. AI_ENABLED=false remains fully supported;
setting it true does not make AI calls or claim AI coverage.

## Privacy

Credentials are excluded from serialization/logging. No cookies, headers or
browser storage are exported. HTML snippets strip values, scripts and arbitrary
attributes. Report URLs redact query values. Screenshots mask inputs, declared
private regions (data-private/data-sensitive), and supplied credentials. Other
sensitive page data can remain; restrict report access and disable visual.enabled
when screenshots are inappropriate. No evidence is sent to an AI provider.

## Demo and checks

Set A11Y_USERNAME to an email-shaped test user and A11Y_PASSWORD to a test password.
Run `python -m demo_app.server --interactive` in another shell with those same variables, then
scan `http://127.0.0.1:8765/`. The local-only demo intentionally contains defects.

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
.\.venv\Scripts\python -m mypy
```

Real Chromium/axe integration tests require installed browser binaries and are
not silently skipped. With sandbox temp restrictions, pass a fresh workspace
`--basetemp=.pytest_cache/my-run` to pytest.

See [architecture](docs/architecture.md), [crawler](docs/crawler.md),
[accessibility](docs/accessibility.md), and [development](docs/development.md), and [Phase 2 design](docs/phase2.md).

## Applications with SSO sign-in

The browser keeps sign-in trust separate from crawl scope. Before crawling, it
recognizes top-level HTTPS OAuth/OIDC authorization redirects with a client ID,
supported response type, and callback on the configured application's origin.
The discovered provider is allowed for that sign-in flow, including username-first
forms. This works across providers without hardcoded website names. Normal crawling
still follows `crawl.same_origin_only`; external links are not added to crawl scope.

Start the launcher and enter your application URL, for example `https://app.example.com/`.
Standard OAuth/OIDC authorization redirects are detected automatically. For a
nonstandard trusted sign-in flow, pass the provider's exact origin (repeat the
option for multiple providers):

```powershell
.\launch-agent.cmd scan --auth-origin https://identity.example.com
```

The launcher then prompts for your application URL and credentials as usual.
Use the application URL as the starting URL. Do not disable same-origin crawling
to fix SSO. Configuration files can instead set `authentication.allowed_origins`
to a list of trusted HTTP(S) origins without paths, query strings or fragments.
CAPTCHA and MFA still require manual handling; an origin allowance does not bypass them.

If a redirect cannot be recognized or explicitly trusted, the report names the
blocked origin as `authentication_origin_not_trusted` and explains the configuration
needed, rather than only reporting missing login fields.

## Highlighted screenshot evidence

The HTML report groups each rule into one issue card and consolidates repeated
observations of the same affected element while retaining its URLs and states.
Screenshots appear once in a shared gallery; identical image files are shown once.
Affected-element links open the relevant screenshot and marker. The complete raw
per-state scanner results remain available in `results.json`.

Numbered red boxes mark violations; amber boxes mark items requiring review.
Multiple rules for one element share a marker. Inputs, private regions and supplied
credentials remain masked.
Missing, hidden, ambiguous and unsupported frame targets receive a note instead of
an invented highlight. A screenshot shows at most 100 distinct highlighted targets.
Highlights are removed before further crawling. Run a new scan to create this evidence;
existing reports and screenshots are not rewritten.

## Blocking input during a crawl

The interactive launcher watches the rendered page after authentication, on each
newly navigated page, and after a UI action settles. A public page or an application
with no blocking dialog continues automatically. Ordinary public forms, nonmodal
panels, and dialogs with a usable Close, Cancel, or Skip action do not pause the crawl.
If a visible nondismissible modal or popup-style overlay prevents access, the terminal
describes its title, field labels, and available choices without reading current input
values. After a successful login, an incomplete required setup form can also be
treated as a gate when no usable application navigation is available. Complete the
controls in the open Chromium window, including its Continue or Confirm button, then
press Enter in the terminal. The scanner rechecks for follow-up dialogs, waits for the
page to settle, saves the selected session so it survives replay, and resumes crawling.

For a headless command-line scan, add `--assist-input` to make Chromium visible and
enable the terminal handoff. A configuration that already sets `browser.headless:
false` also enables the handoff. Applications with unusual non-semantic overlays can
set `input_assistance.blocking_selectors` in YAML. These selectors identify blocking
containers; they do not contain the value the user must choose.

Username and password remain optional. Supply them for a login page. Leave the
username empty for a public website; no credential prompt occurs inside Chromium.

A run with only refreshed sources and zero successful interactions is marked partial.

## WCAG and draft ACR reporting

This branch preserves the existing crawler and adds an **offline, human-reviewed
WCAG/VPAT/ACR preparation workflow**. No new requirements or API key are needed.

- Map saved scan findings to the full selected WCAG checklist.
- Record reviewer decisions, methods, dates, remarks and evidence.
- Export a draft ACR dashboard and JSON; download edited review records from the dashboard.
- Compare two saved scans and optionally their reviewed conformance assessments.
- Keep absent coverage, incomplete tests and uncertain matches separate from verified fixes.

Start with `launch-agent.cmd acr --help`, then follow [the walkthrough](docs/conformance.md).
Automatic passes are **not** conformance decisions. Outputs remain drafts, not certification
or complete Section 508 / EN 301 549 reports. The current crawler command is unchanged.
