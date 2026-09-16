import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

from playwright.async_api import async_playwright

from accessibility_agent.models import ApplicationInfo, Report, ScanMetadata
from accessibility_agent.reporting.writers import HTMLReportWriter


def test_normal_report_vpat_downloads_and_required_product_metadata(tmp_path):
    async def check():
        report = Report(
            application=ApplicationInfo(url="https://example.test/", title="Example application"),
            scan=ScanMetadata(scan_id="vpat-browser", started_at=datetime.now(UTC),
                              status="completed"),
        )
        path = await HTMLReportWriter().write(report, tmp_path)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1360, "height": 1000})
                errors, remote_requests = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: remote_requests.append(request.url)
                        if request.url.startswith(("https:", "http:")) else None)
                await page.goto(path.as_uri())
                await page.get_by_role("link", name="VPAT / ACR", exact=True).click()
                assert page.url.endswith("#vpat-acr")
                section = page.locator("#vpat-acr")
                await section.screenshot(path=tmp_path / "normal-report-vpat.png")
                await section.locator("summary").click()
                await page.get_by_label("Search vpat criteria").fill("4.1.2")
                assert await page.locator("#vpat-criteria tbody tr:visible").count() == 1
                for label, filename in (
                    ("Download official blank VPAT template (.docx)", "vpat-template.docx"),
                    ("Download scan-assisted draft ACR (.docx)", "draft-acr.docx"),
                ):
                    async with page.expect_download() as info:
                        await section.get_by_role("link", name=label, exact=True).click()
                    download = await info.value
                    assert download.suggested_filename == filename
                    with ZipFile(await download.path()) as document:
                        assert "word/document.xml" in document.namelist()
                await section.get_by_role("link", name="Open the ACR review editor").click()
                assert page.url.endswith("acr-report.html")
                await page.get_by_label("Product version (required)").fill(" ")
                await page.get_by_role("button", name="Export review JSON").click()
                assert "complete version" in await page.locator("#export-status").inner_text()
                assert await page.get_by_label("Product version (required)").evaluate(
                    "element => element === document.activeElement"
                )
                await page.get_by_label("Product version (required)").fill("2.0")
                await page.get_by_label("Application identifier (required)").fill("example-qa")
                await page.get_by_label("Evaluation scope (required)").fill("Reviewed home page")
                async with page.expect_download() as info:
                    await page.get_by_role("button", name="Export review JSON").click()
                downloaded = await info.value
                review = json.loads(Path(await downloaded.path()).read_text(encoding="utf-8"))
                assert review["product"]["version"] == "2.0"
                assert review["product"]["application_id"] == "example-qa"
                assert review["product"]["scope"] == "Reviewed home page"
                assert all(row["conformance"] is None for row in review["criteria"])
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.locator("#product").scroll_into_view_if_needed()
                assert await page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                assert not errors
                assert not remote_requests
            finally:
                await browser.close()

    asyncio.run(check())
