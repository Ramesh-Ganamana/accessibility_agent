"""Exercise actual file:// dashboards, local image loading and downloadable review contracts."""

import asyncio
import base64
import json
from datetime import UTC, datetime

from playwright.async_api import async_playwright

from accessibility_agent.conformance.models import DraftACR, ReviewSet
from accessibility_agent.conformance.writers import write_acr, write_comparison


def dashboard_document():
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    return DraftACR.model_validate(
        {
            "generated_at": now,
            "report_date": now.date(),
            "source_scan_id": "browser-fixture",
            "source_sha256": "b" * 64,
            "application_url": "https://example.test/",
            "scan_status": "completed",
            "product": {
                "application_id": "example",
                "name": "Example",
                "version": "1.0",
                "scope": "Public pages",
            },
            "wcag_version": "2.2",
            "level": "AAA",
            "evaluation_methods": ["Automated scan"],
            "coverage": {},
            "limitations": [],
            "unmapped_finding_ids": [],
            "evidence": [
                {
                    "evidence_id": "screenshot-1",
                    "kind": "screenshot",
                    "state_id": "state-1",
                    "path": "fixture.png",
                    "description": "Local fixture screenshot",
                }
            ],
            "criteria": [
                {
                    "criterion_id": identifier,
                    "title": title,
                    "level": level,
                    "url": "https://www.w3.org/TR/WCAG22/",
                    "automated_status": "observations_only",
                    "observations": {"pass": 3},
                    "review": {"criterion_id": identifier},
                    "checklist": ["Complete manual keyboard testing"],
                    "findings": [
                        {
                            "finding_id": "f-1",
                            "rule_id": "image-alt",
                            "title": "Image alternative",
                            "classification": "NEEDS_REVIEW",
                            "source": "AXE",
                            "occurrence_count": 1,
                            "urls": ["https://example.test/"],
                            "selectors": ["#logo"],
                            "evidence_ids": ["screenshot-1"],
                        }
                    ]
                    if identifier == "1.1.1"
                    else [],
                }
                for identifier, title, level in [
                    ("1.1.1", "Non-text Content", "A"),
                    ("1.4.3", "Contrast (Minimum)", "AA"),
                    ("1.4.6", "Contrast (Enhanced)", "AAA"),
                ]
            ],
        }
    )


def test_file_dashboard_review_export_filters_and_responsive_keyboard_access(tmp_path):
    async def check():
        document = dashboard_document()
        (tmp_path / "fixture.png").write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZk"
                "AAAAASUVORK5CYII="
            )
        )
        path = write_acr(document, tmp_path / "acr", tmp_path)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1360, "height": 900})
                failures = []
                network = []
                page.on("pageerror", lambda error: failures.append(str(error)))
                page.on(
                    "request",
                    lambda request: (
                        network.append(request.url)
                        if request.url.startswith(("http:", "https:"))
                        else None
                    ),
                )
                await page.goto(path.as_uri())
                await page.keyboard.press("Tab")
                assert await page.get_by_role("link", name="Skip to review overview").evaluate(
                    "element => element === document.activeElement"
                )
                assert await page.locator("#pending-count").inner_text() == "3"
                assert await page.locator('[data-field="conformance"]').evaluate_all(
                    "elements => elements.every(element => element.value === '')"
                )
                a_row = page.locator('[data-criterion="1.1.1"]')
                aaa_row = page.locator('[data-criterion="1.4.6"]')
                assert await a_row.locator('option[value="Not Evaluated"]').count() == 0
                assert await aaa_row.locator('option[value="Not Evaluated"]').count() == 1
                await page.get_by_label("Search criteria and evidence").fill("Minimum")
                assert await page.locator("#filter-count").inner_text() == "1 of 3 criteria shown"
                await page.get_by_label("Search criteria and evidence").fill("")
                await page.get_by_label("Criterion level", exact=True).select_option("AAA")
                assert await page.locator("#filter-count").inner_text() == "1 of 3 criteria shown"
                await page.get_by_label("Criterion level", exact=True).select_option("")
                await (
                    a_row.get_by_role("group")
                    .filter(has=page.get_by_text("Finding details and screenshots", exact=False))
                    .locator("summary")
                    .click()
                )
                image = a_row.get_by_role("img", name="Local fixture screenshot")
                await image.scroll_into_view_if_needed()
                await page.wait_for_function(
                    "Array.from(document.images).some(image => "
                    "image.complete && image.naturalWidth > 0)"
                )
                await a_row.get_by_label("Conformance for 1.1.1").select_option("Supports")
                await page.get_by_role("button", name="Export review JSON").click()
                assert "complete reviewer" in await page.locator("#export-status").inner_text()
                assert await a_row.get_by_label("Reviewer", exact=True).evaluate(
                    "element => element === document.activeElement"
                )
                await a_row.get_by_label("Reviewer", exact=True).fill("Example Reviewer")
                await a_row.get_by_label("Review date", exact=True).fill("2026-09-09")
                await a_row.get_by_label("Evaluation methods", exact=True).fill(
                    "Manual screen reader"
                )
                await a_row.get_by_label("Remarks and explanations for 1.1.1").fill(
                    '</script><script>alert("escaped")</script> '
                    "Observed appropriate alternative text."
                )
                await a_row.get_by_label("Evidence references", exact=True).fill(
                    "test-1\nscreenshot-1"
                )
                await page.get_by_label("Organization / report owner").fill("Example QA")
                assert await page.locator("#pending-count").inner_text() == "2"
                await page.get_by_label("Review status", exact=True).select_option("reviewed")
                assert await page.locator("#filter-count").inner_text() == "1 of 3 criteria shown"
                async with page.expect_download() as download_event:
                    await page.get_by_role("button", name="Export review JSON").click()
                download = await download_event.value
                downloaded = tmp_path / download.suggested_filename
                await download.save_as(downloaded)
                review = ReviewSet.model_validate_json(downloaded.read_text(encoding="utf-8"))
                assert review.source_sha256 == document.source_sha256
                assert review.product.organization == "Example QA"
                assert review.criteria[0].conformance == "Supports"
                assert review.criteria[0].evidence == ["test-1", "screenshot-1"]
                assert review.criteria[1].conformance is None
                assert review.criteria[0].remarks.startswith("</script><script>")
                assert not failures and not network
                await page.get_by_label("Review status", exact=True).select_option("")
                await page.set_viewport_size({"width": 390, "height": 844})
                assert await page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                await a_row.get_by_label("Conformance for 1.1.1").focus()
                await page.keyboard.press("Tab")
                assert await a_row.get_by_label("Reviewer", exact=True).evaluate(
                    "element => element === document.activeElement"
                )
                assert await page.get_by_role("table").count() == 1
                await page.screenshot(path=str(tmp_path / "acr-mobile.png"), full_page=True)
            finally:
                await browser.close()

    asyncio.run(check())


def test_file_comparison_status_search_and_mobile_layout(tmp_path):
    comparison = {
        "baseline_scan_id": "old",
        "current_scan_id": "new",
        "warnings": ["Tested states only"],
        "counts": {"not_verified": 1, "still_present": 1},
        "items": [
            {
                "key": "one",
                "status": "not_verified",
                "title": "Image alternative",
                "rule_id": "image-alt",
                "url": "javascript:alert(1)",
                "reason": "Page not visited again",
                "baseline_occurrences": 1,
                "current_occurrences": 0,
            },
            {
                "key": "two",
                "status": "still_present",
                "title": "Contrast",
                "rule_id": "color-contrast",
                "url": "https://example.test/",
                "reason": "Observed in both scans",
                "baseline_occurrences": 2,
                "current_occurrences": 2,
            },
        ],
    }

    async def check():
        path = write_comparison(comparison, tmp_path)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 390, "height": 844})
                failures = []
                page.on("pageerror", lambda error: failures.append(str(error)))
                await page.goto(path.as_uri())
                assert await page.locator("#comparison-count").inner_text() == "2 of 2 items shown"
                await page.get_by_label("Change status", exact=True).select_option("not_verified")
                assert await page.locator("#comparison-count").inner_text() == "1 of 2 items shown"
                await page.get_by_label("Search findings", exact=True).fill("Contrast")
                assert await page.locator("#comparison-empty").is_visible()
                await page.get_by_label("Change status", exact=True).select_option("")
                assert await page.locator("#comparison-count").inner_text() == "1 of 2 items shown"
                assert await page.locator('a[href^="javascript:"]').count() == 0
                assert await page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                assert not failures
            finally:
                await browser.close()
        assert json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8")) == comparison

    asyncio.run(check())
