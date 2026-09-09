# Development

## Current Phase 2 verification

Windows/Python 3.13.1: **53 tests passed** in the full suite, including all Phase 1
regressions and new Chromium integration tests. Ruff lint/format passed (52 Python
files), strict mypy passed (34 source files), and wheel contents were verified.
The full run used `--basetemp=.pytest_cache/phase2-full1`.

The interactive demo produced 23 distinct scanned states at one URL, 119 recorded
actions and 5 finding groups. Its partial status is intentional: it includes a
broken page, blocked actions/HTTP writes and an action budget. The HTML report was
rendered, inspected and checked for working replay details. Outputs are under
`reports/phase2-demo/` and are ignored by git.

New tests cover SPA links, menus, tabs, dialogs, accordions, native/custom dropdowns,
configured input privacy, state deduplication, replay divergence, budgets, ignored
clocks, asynchronous busy states, one-time allowances, HTTP writes, and session
storage restoration without resetting later navigation state. No Phase 3 keyboard
testing or AI implementation was added. Python 3.12 was not separately exercised.

Use Python 3.12+ and install .[dev] in a virtual environment. Install Chromium via
`python -m playwright install chromium`. Tests do not silently skip browser checks.
Run pytest, Ruff lint/format checks and strict mypy as shown in README.

For sandbox temp restrictions use a fresh workspace-local
`--basetemp=.pytest_cache/<run-name>`. Tests start temporary loopback servers and
stop them in teardown. Credentials are synthetic; report/log leakage is tested.

Concrete Phase 1 implementations live in browser, authentication, crawler,
accessibility, evidence, findings, reporting and agent. Models/interfaces remain
centralized. BrowserSession exposes lifecycle, page access, guarded navigation
and crawl-policy activation. Authenticator/scanner dependencies support injection
for resilience testing. Other engines/providers remain future work.

The demo is a source-tree development utility, never a production service. Its
later-phase fixtures do not imply Phase 1 interaction testing. Generated reports
and project-local browser downloads are git-ignored. The axe bundle is pinned with
license/provenance/checksum. Python dependency ranges are not a release lock.
Verify wheel resources when changing package-data paths.

## Phase 1 verification

Verified on Windows/Python 3.13.1 with Chromium: 41 tests passed, Ruff lint and
format checks passed (48 Python files), and strict mypy passed (32 source files).
The full suite used `--basetemp=.pytest_cache/phase1-run3`. Python 3.12 remains
declared supported but was not separately exercised in this environment.

Browser tests cover cookie-backed authentication, username-first discovery,
selector overrides, CAPTCHA/MFA indicators, cross-origin form blocking, public
pages, axe findings, screenshot evidence, CLI output, failure continuation,
budgets and direct/chained destructive redirects. The Chromium adapter uses CDP
Fetch interception because normal Playwright route continuation may not invoke
the callback for every redirect hop. Unsupported frame/pop-up navigation is blocked.

An authenticated local demo scan completed with 2 states and 10 finding groups;
its offline report was rendered and visually inspected. The wheel resource check
verified the axe bundle, checksum manifest, license, inventory script and template.
