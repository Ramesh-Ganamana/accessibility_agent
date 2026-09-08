# Phase 2 implementation design

The interactive crawler is the default; `crawl.mode: links` retains the Phase 1
navigation crawler. Both feed the same axe scanner, evidence collector, finding
repository and report writers. No keyboard accessibility or AI engine is added.

Each unique state has a versioned URL/DOM/text/UI fingerprint and an in-memory
replay path. The browser captures authenticated cookies, localStorage, IndexedDB
and the entry origin's sessionStorage in memory. Before exploring another branch,
create a fresh context from this checkpoint, navigate to the entry URL and replay
the path. Verify the entry and every intermediate state, as well as the target
element's semantics. Divergence is an unscanned area, never an inferred success.
Server state cannot be rolled back; unsafe actions are never automatically replayed.

Discover semantic buttons, tabs, menu items, summaries, expanders, dialog controls,
comboboxes, select options, and form controls. Strong UI semantics or conservative
navigation/open/close names permit exploration. Missing test data, unknown actions,
submits, disabled controls, downloads and external destinations are skipped.
Configured form values permit non-sensitive text filling; never fill password,
file or hidden controls during crawling. Exact action allowances execute once and
produce terminal states. Unsafe HTTP methods require an explicit URL allowance.

Snapshots include checked/selected/expanded/open state and hashed form values.
Both crawl modes wait for document load, finished finite requests, loaded fonts
and visible images, a rendered document, and no visible busy/loading indicators.
After `network_idle_ms` of network quiet (default 500 ms), DOM and layout must stay
unchanged for `dom_quiet_ms` (default 1000 ms). The bounded readiness deadline is
`stability_timeout_ms` (default 60000 ms); timeouts record
`page_readiness_timeout` as untested, without scanning a loading shell.
Optional `ready_selector` and `loading_selectors` add application-specific signals;
ignored selectors exclude volatile regions from stability checks. Streaming
connections need not close, and finite waits cannot predict every future update.
Budgets cover depth, unique states, actions per state, total actions, options per
select, replay steps and duration. Every frontier entry ends with an outcome.
Only distinct fingerprints are scanned; all transitions and occurrences survive.

Frames, popups and shadow-root interaction remain explicitly unsupported. Native
select testing changes selection; it does not capture an OS dropdown popup. Form
validation/keyboard/AX testing and AI remain later phases.
