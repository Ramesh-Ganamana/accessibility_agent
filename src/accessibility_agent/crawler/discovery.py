"""Read-only inventory shared by link and interaction crawl phases."""

from importlib.resources import files
from typing import Any, cast

from playwright.async_api import Error, Frame, Locator, Page

# Bounded inventory; no values, hidden inputs, script bodies or arbitrary data attributes.
INVENTORY = (
    files("accessibility_agent.crawler").joinpath("inventory.js").read_text(encoding="utf-8")
)


async def inventory(
    page: Page,
    ignored: list[str] | None = None,
    *,
    loading_selectors: list[str] | None = None,
    ready_selector: str = "",
    check_readiness: bool = False,
) -> dict[str, Any]:
    """Inventory the page, open shadow roots, and readable child frames.

    Playwright already pierces open shadow roots for CSS locators, but a
    selector alone is ambiguous when the same component is rendered in a
    frame or several shadow roots.  The returned paths are therefore kept
    separately and consumed by :func:`locator_for_item`.  Cross-origin and
    detached frames are reported as unsupported instead of failing the page.
    """

    options = {
        "ignored": ignored or [],
        "loading_selectors": loading_selectors or [],
        "ready_selector": ready_selector,
        "check_readiness": check_readiness,
    }
    main: dict[str, Any] = await page.evaluate(INVENTORY, options)
    for item in main.get("elements", []):
        item.setdefault("frame_path", [])
    main.setdefault("unsupported_frames", 0)
    main.setdefault("shadow_roots", 0)

    # The DOM inventory script runs in each frame so same-origin embedded
    # applications participate in normal planning and state fingerprints.
    frame_results: list[dict[str, Any]] = []
    children = [frame for frame in page.frames if frame != page.main_frame]
    main["unsupported_frames"] += max(0, len(children)-32)
    for frame in children[:32]:
        try:
            if not await readable_frame(frame):
                main["unsupported_frames"] += 1
                continue
            path = await frame_path(frame)
            # An invisible/ignored/modal-blocked embedding must not expose
            # controls that are absent from the effective application state.
            embedding = await frame.frame_element()
            try:
                blocked = await embedding.evaluate(
                    """(e, ignored) => {
                      for(let n=e;n;n=n.parentElement||n.getRootNode()?.host) {
                        if(n.matches('[inert],[aria-hidden=true]') ||
                          ignored.some(s=>n.matches(s)) || !n.getClientRects().length ||
                          getComputedStyle(n).visibility==='hidden') return true;
                      }
                      return false;
                    }""",
                    ignored or [],
                )
            finally:
                await embedding.dispose()
            if blocked:
                main["unsupported_frames"] += 1
                continue
            frame_data: dict[str, Any] = await frame.evaluate(
                INVENTORY, options | {"ready_selector": ""}
            )
        except Error:
            main["unsupported_frames"] += 1
            continue
        for item in frame_data.get("elements", []):
            item["frame_path"] = path
            if main.get("dialogs"):
                item["blocked_by_modal"] = True
        frame_data["scope"] = [path, frame.url]
        frame_results.append(frame_data)

    for data in frame_results:
        main["elements"].extend(data.get("elements", []))
        main["structure"].append(["frame", data["scope"]])
        main["structure"].extend(data.get("structure", []))
        main["form_values"].extend(data.get("form_values", []))
        main["dialogs"].extend(data.get("dialogs", []))
        main["menus"].extend(data.get("menus", []))
        main["tabs"].extend(data.get("tabs", []))
        main["forms"].extend(data.get("forms", []))
        combined = main.get("text", "") + " " + data.get("text", "")
        main["truncated"] = main.get("truncated", False) or len(combined)>100000
        main["text"] = combined[:100000]
        main["truncated"] = main.get("truncated", False) or data.get("truncated", False)
        main["busy"] = main.get("busy", False) or data.get("busy", False)
        main["render_ready"] = main.get("render_ready", False) or data.get("render_ready", False)
        main["shadow_roots"] += int(data.get("shadow_roots", 0))
        if check_readiness:
            for key in ("document_complete", "fonts_loaded", "images_loaded"):
                main["readiness"][key] &= data["readiness"][key]
            main["readiness"]["layout"].extend(data["readiness"]["layout"])

    main["frames"] = max(0, len(page.frames) - 1)
    main["unsupported_frames"] += sum(
        int(item.get("unsupported_frames", 0)) for item in frame_results
    )
    if len(main["elements"]) > 2000:
        main["truncated"] = True
        main["elements"] = main["elements"][:2000]
    if len(main["structure"]) > 10000:
        main["truncated"] = True
        main["structure"] = main["structure"][:10000]
    main["shadow"] = bool(main.get("shadow_roots", 0))
    # Include frame/shadow scope only when the page actually has scoped
    # controls.  Ordinary pages retain the original UI fingerprint shape,
    # avoiding needless state-id churn while scoped controls remain distinct.
    if any(
        item.get("frame_path") or item.get("shadow_path")
        for item in main["elements"]
    ):
        main["ui"] = [
            [
                item["selector"],
                item.get("role", ""),
                item.get("accessible_name", ""),
                item.get("attributes", {}),
                item.get("disabled", False),
                item.get("options", []),
                item.get("frame_path", []),
                item.get("shadow_path", []),
            ]
            for item in main["elements"]
            if item.get("visible")
        ]
    return main


async def readable_frame(frame: Frame) -> bool:
    """Include only documents with the same effective origin as the top page."""
    try:
        return bool(await frame.evaluate(
            """() => { try { return !!top.document && window.origin === top.origin; }
              catch { return false; } }"""
        ))
    except Error:
        return False


async def frame_path(frame: Frame) -> list[str]:
    """Return CSS selectors from the page root to ``frame``."""

    chain: list[str] = []
    current = frame
    while current.parent_frame is not None:
        if len(chain) >= 16:
            raise Error("Frame nesting limit")
        element = await current.frame_element()
        try:
            selector = await element.evaluate(
            """e => {
              const parts=[];
              while(e && e.nodeType===1) {
                const tag=e.localName;
                const siblings=e.parentNode?.children
                  ? [...e.parentNode.children].filter(x=>x.localName===tag) : [e];
                parts.unshift(tag+':nth-of-type('+(siblings.indexOf(e)+1)+')');
                if(!e.parentElement && e.getRootNode()?.host) {
                  parts.unshift('>>');
                  e=e.getRootNode().host;
                  continue;
                }
                e=e.parentElement;
              }
              return parts.join(' > ').replaceAll(' > >> > ', ' >> ');
            }"""
            )
        finally:
            await element.dispose()
        chain.insert(0, selector)
        current = current.parent_frame
    return chain


def locator_for_item(page: Page, item: dict[str, Any]) -> Locator:
    """Build a Playwright locator for an inventory item in any open scope."""

    scope: Any = page
    for frame_selector in item.get("frame_path", []):
        scope = scope.frame_locator(frame_selector)
    for host_selector in item.get("shadow_path", []):
        scope = scope.locator(host_selector)
    return cast(Locator, scope.locator(item["selector"]))
