# State crawler

`crawl.mode: interactive` uses InteractiveCrawler; `links` preserves InitialCrawler
for Phase 1 compatibility. Both share graph/counter conventions and feed the same
scan/evidence/reporting pipeline. Interactive mode uses Playwright for bounded
click, keyboard activation and hover probes; links mode remains navigation-only.
An AI planner can be added as an optional classifier for ambiguous controls, but
the deterministic browser and safety policy remain authoritative.

Interactive discovery produces actions for semantic links, buttons, menus, tabs,
options, comboboxes, summaries, selects, checkboxes/radios and configured inputs.
Actions retain safety/outcome/reason, option index and replay count. State records
include versioned fingerprints, UI inventories and replay action IDs. Raw test
values live only in in-memory replay steps, never the graph.

When restoration is needed, a branch starts from a new context seeded from authenticated cookies,
localStorage, IndexedDB and entry-origin sessionStorage. Session storage is seeded
once before app scripts and may evolve on subsequent navigations. Every replay
hop must match the expected fingerprint and target semantics. Divergence fails
that branch while other pending branches continue. Server state is not restored.

Safe HTTP(S) links use an independent, deduplicated URL frontier rather than UI
replay. This includes SPA hash routes, links in collapsed menus, and links that
normally open a new tab (visited in the existing guarded browser instead).
Links with inline JavaScript handlers retain UI replay so their custom behavior is exercised.
Ordinary in-page anchors are deduplicated without dropping SPA hash routes.
URL visits alternate with UI actions; duplicate and policy-skipped links do
not consume the per-state execution allowance. Discovered hidden links are stored
separately from visible controls, and direct visits never count as exercised controls.

The graph records `direct_navigation`. Further buttons and forms on the destination
still require verified replay. Request guards remain active; a blocked background
request is recorded as `navigation_background_requests_blocked` without discarding
a successfully loaded document. Failed document navigation and HTTP errors still
fail that visit. Such coverage is partial because blocked requests can affect content.
No site-specific hostnames or selectors are required. This frontier covers discovered
links, not unlinked URLs, inaccessible accounts, or every possible application state.

Fingerprints normalize URL host/default port and text whitespace, retaining query
and SPA fragments in memory. They combine DOM structure, text, roles/names,
expanded/selected/checked/open state and form values. Output only contains hashes,
not raw values. Ignored selectors can remove volatile regions.

Both crawlers use the same readiness check after navigation and UI changes:
document load, no pending finite requests, loaded fonts and visible images, no
visible busy/loading indicators, and a rendered document. After 500 ms of network
quiet (`network_idle_ms`), the DOM and layout must remain unchanged for 1000 ms
(`dom_quiet_ms`). `settle_ms` adds an initial delay after the load event.
`stability_timeout_ms` bounds the entire check at 60000 ms by default. A timeout
records `page_readiness_timeout` and pending readiness signals as an untested area;
it does not produce a scanned state from the loading shell.

An optional `ready_selector` must match a visible element. Additional
`loading_selectors` must have no visible matches. Use these for an application's
known completion signal and custom skeletons/spinners. Finite quiet windows cannot
predict later asynchronous updates. WebSocket and event-stream connections are
excluded from pending requests; their rendered output still undergoes stability
checks. Continuous polling or animation can reach the readiness deadline.

Breadth-first expansion scans each unique fingerprint once and preserves every
transition, including duplicate/no-change results. Budgets cap unique states,
original actions, depth, actions per state, select options and replay length.
Cancellation finalizes remaining frontier entries. Coverage counts observed
states and original successful UI actions, excluding replay repetitions.

Positive control semantics permit actions only after destructive names/submits,
origin, disabled/inert/modal-background and sensitive-field checks. Unknown
controls require explicit action IDs. Allowed non-safe actions are terminal and
never replayed. HTTP writes require exact URL allowances and also make the action
terminal. CDP checks redirect hops, including unsafe methods. GET side effects,
WebSocket traffic and external server state cannot be rolled back.

Open shadow roots and readable same-origin frames are inventoried and replayed.
Cross-origin or detached frames, closed shadow roots, native OS popups and
complex touch gestures remain unscanned. Closed shadow roots cannot be reliably
detected. Canvas elements with button/link semantics can be clicked as a whole;
arbitrary canvas hit regions and drag destinations require a configured target.
Drag-only controls are recorded as `drag_needs_target`, without guessing a drop.
Native selects test changed selection, not the operating-system
popup. Forms are inventoried and optionally filled; automatic validation is not
claimed. Limits: 2,000 inventoried controls, 10,000 fingerprint DOM elements,
100,000 text characters; truncation is reported. No full-site coverage is claimed.
The axe scan includes readable iframe documents; cross-origin restrictions can
still make individual frame rules incomplete.

## Interaction coverage

The scheduler gives a UI action a turn before navigation and after every two URL
visits while both queues have work. It reuses the loaded page when its fingerprint
still matches the planned source. Changed root or directly loaded source states are
scanned and replanned; stale actions are skipped with `source_refreshed`, never credited.
Intermediate replay mismatches still fail the branch.

Common search, notification, user-menu and theme buttons are recognized alongside
ARIA controls. Search inputs receive a synthetic query without form submission;
other fields still use `crawl.form_values`. Negative tabindex containers without
control semantics are excluded from the control count. Successful UI changes can
be scanned despite blocked background requests, with a partial-coverage warning.
Controls used counts successful interactions, including bounded keyboard activation
and hover, not direct visits or replay repetitions. Keyboard activation does not
establish keyboard accessibility conformance.
