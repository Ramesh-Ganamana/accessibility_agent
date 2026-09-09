"""Read-only inventory for the initial link-based crawl phase."""

from importlib.resources import files
from typing import Any

from playwright.async_api import Page

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
    result: dict[str, Any] = await page.evaluate(
        INVENTORY,
        {
            "ignored": ignored or [],
            "loading_selectors": loading_selectors or [],
            "ready_selector": ready_selector,
            "check_readiness": check_readiness,
        },
    )
    return result
