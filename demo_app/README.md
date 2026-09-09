# Local inaccessible demo

Set A11Y_USERNAME (email format) and A11Y_PASSWORD, then run
`python -m demo_app.server --port 8765`. It binds only to 127.0.0.1 and keeps
sessions in memory. Scan http://127.0.0.1:8765/ with matching credentials.
/public is available without login.

Phase 1 checks missing alt, labels, names, contrast and ARIA. The page also has
keyboard trap/focus/dialog/dynamic fixtures reserved for later phases. Their
presence does not imply the agent currently tests these interactions. Never
deploy this demo as a real application.

Use `python -m demo_app.server --interactive` for the Phase 2 fixture. It adds SPA
links, menus, tabs, modal, accordion, native/custom dropdown and input paths.
Deliberately unsafe and broken controls exercise partial-scan reporting.
