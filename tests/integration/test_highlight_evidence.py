import asyncio
import base64
from datetime import UTC, datetime

import pytest

from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.config.settings import load_settings
from accessibility_agent.evidence.evidence_manager import StateEvidenceCollector
from accessibility_agent.models import (
    ApplicationInfo,
    Finding,
    Graph,
    Occurrence,
    Report,
    ScanMetadata,
    State,
)
from accessibility_agent.reporting.writers import HTMLReportWriter
from accessibility_agent.utils.privacy import Redactor

MARKUP = """<!doctype html><html lang="en"><title>Evidence fixture</title>
<style>body{margin:40px;font:20px Arial;background:white;color:black}
button,input{display:block;width:220px;height:44px;margin:40px 0}
#review{width:260px;padding:20px;margin-top:30px;background:#ddd}
[data-private]{display:block;width:300px;height:45px;margin:30px 0}
</style><h1>Screenshot evidence</h1><button id="bad">Affected button</button>
<input id="field" value="private-input-fixture"><div id="review">Review this region</div>
<div data-private>private-region-fixture</div><p id="secret">supplied-secret-fixture</p>
<div id="hidden" hidden>Hidden target</div><div id="host"></div>
<script>document.querySelector('#host').attachShadow({mode:'open'}).innerHTML=
'<button id="inside" style="width:180px;height:50px;margin:30px">Shadow control</button>'
</script></html>"""


def finding(rule, target, classification="VIOLATION"):
    return Finding(
        id=rule,
        rule_id=rule,
        title="Finding " + rule,
        source="AXE",
        classification=classification,
        occurrences=[
            Occurrence(
                url="https://example.test/",
                state_id="fixture-state",
                element="axe-node",
                selector=str(target),
                target=target,
            )
        ],
    )


def fixture_state():
    return State(
        state_id="fixture-state", url="https://example.test/", title="Evidence", dom_hash="dom"
    )


def test_screenshot_markers_masks_report_links_and_cleanup(tmp_path, monkeypatch):
    async def run():
        settings = load_settings(environ={"A11Y_URL": "https://example.test/"})
        settings.browser.viewport.width = 900
        settings.browser.viewport.height = 700
        session = ChromiumSession()
        await session.open(settings)
        try:
            await session.page.set_content(MARKUP)
            original = await session.page.content()
            targets = [
                finding("button-name", ["#bad"]),
                finding("duplicate-target", ["#bad"]),
                finding("label", ["#field"]),
                finding("color-contrast", ["#review"], "NEEDS_REVIEW"),
                finding("shadow", [["#host", "#inside"]]),
                finding("missing", ["#missing"]),
                finding("hidden", ["#hidden"]),
                finding("frame", ["iframe", "#inside"]),
                finding("invalid", ["["]),
            ]
            collector = StateEvidenceCollector(
                tmp_path, Redactor(["supplied-secret-fixture"]), True
            )
            # Observe closed-shadow callouts at capture time without changing their isolation.
            await session.page.evaluate(
                """() => {
                    const attach = Element.prototype.attachShadow;
                    Element.prototype.attachShadow = function(options) {
                        const shadow = attach.call(this, options);
                        if (this.hasAttribute('data-a11y-evidence-overlay'))
                            window.evidenceShadow = shadow;
                        return shadow;
                    };
                }"""
            )
            screenshot = session.page.screenshot
            captured_labels = []

            async def inspect_screenshot(**kwargs):
                captured_labels.extend(
                    await session.page.evaluate(
                        """() => [...window.evidenceShadow.querySelectorAll('.label')]
                            .map(node => node.textContent)"""
                    )
                )
                return await screenshot(**kwargs)

            monkeypatch.setattr(session.page, "screenshot", inspect_screenshot)
            state = fixture_state()
            evidence = await collector.capture(session, state, targets)
            assert captured_labels == [
                "#1 Finding button-name#1 Finding duplicate-target",
                "#2 Finding label",
                "#3 Needs review: Finding color-contrast",
                "#4 Finding shadow",
            ]
            assert await session.page.content() == original
            assert await session.page.locator("[data-a11y-evidence-overlay]").count() == 0
            nodes = [item.occurrences[0] for item in targets]
            assert [node.screenshot_marker for node in nodes[:5]] == [1, 1, 2, 3, 4]
            assert all(node.screenshot_marker is None for node in nodes[5:])
            assert all(node.screenshot_note for node in nodes[5:])
            assert "Frame targets" in nodes[7].screenshot_note
            assert len([item for item in evidence if item.kind == "screenshot"]) == 1

            # Inspect real Chromium screenshot pixels, including privacy masks.
            encoded = base64.b64encode((tmp_path / state.screenshot).read_bytes()).decode()
            pixels = await session.page.evaluate(
                """async encoded => {
                    const image = new Image();
                    image.src = 'data:image/png;base64,' + encoded;
                    await image.decode();
                    const canvas = document.createElement('canvas');
                    canvas.width=image.width;canvas.height=image.height;
                    const context=canvas.getContext('2d');context.drawImage(image,0,0);
                    const data=context.getImageData(0,0,image.width,image.height).data;
                    let red=0,amber=0;
                    for(let i=0;i<data.length;i+=4) {
                        if(data[i]===190&&data[i+1]===18&&data[i+2]===60) red++;
                        if(data[i]===154&&data[i+1]===91&&data[i+2]===0) amber++;
                    }
                    const samples=['#field','[data-private]','#secret'].map(selector=>{
                        const r=document.querySelector(selector).getBoundingClientRect();
                        return [...context.getImageData(r.x+5,r.y+5,1,1).data];
                    });
                    const field=document.querySelector('#field').getBoundingClientRect();
                    const inputBorder=[...context.getImageData(field.x-3,field.y+12,1,1).data];
                    return {red,amber,samples,inputBorder};
                }""",
                encoded,
            )
            assert pixels["red"] > 500
            assert pixels["amber"] > 500
            assert pixels["samples"] == [[255, 0, 255, 255]] * 3
            assert pixels["inputBorder"] == [190, 18, 60, 255]
            for node in nodes:
                node.evidence = [item.evidence_id for item in evidence]
            report = Report(
                application=ApplicationInfo(url=state.url),
                scan=ScanMetadata(
                    scan_id="fixture", started_at=datetime.now(UTC), status="completed"
                ),
                findings=targets,
                graph=Graph(states=[state]),
                evidence=evidence,
            )
            path = await HTMLReportWriter().write(report, tmp_path)
            html = path.read_text(encoding="utf-8")
            assert "View affected element in screenshot" in html
            assert 'src="screenshots/fixture-state.png"' in html
            assert html.count("<img ") == 1  # Nine issues share one state screenshot.
            assert "affected element at marker #1" in html
            assert "this occurrence has no highlight" in html
            assert "Frame targets cannot be highlighted" in html
            assert "supplied-secret-fixture" not in html

            async def serve_report(route):
                if route.request.url.endswith(".png"):
                    await route.fulfill(
                        content_type="image/png", body=(tmp_path / state.screenshot).read_bytes()
                    )
                else:
                    await route.fulfill(content_type="text/html", body=html)

            await session.page.route("https://example.test/**", serve_report)
            await session.navigate("https://example.test/accessibility-report.html")
            await session.page.locator(".issue-group").first.evaluate("node => node.open=true")
            await session.page.locator('a[href="#screenshot-1"]').first.click()
            assert await session.page.locator(".shot-preview img").count() == 1
            await session.page.locator(".shot-preview img").first.scroll_into_view_if_needed()
            await session.page.wait_for_function(
                """() => {
                    const image=document.querySelector('.shot-preview img');
                    return image.complete && image.naturalWidth > 0;
                }"""
            )
            await session.page.locator(".shot-details").first.screenshot(
                path=str(tmp_path / "report-highlight-preview.png")
            )
        finally:
            await session.close()

    asyncio.run(run())


def test_screenshot_uses_one_visible_example_per_issue_with_safe_wrapped_titles(
    tmp_path, monkeypatch
):
    async def run():
        session = ChromiumSession()
        settings = load_settings(environ={"A11Y_URL": "https://example.test/"})
        settings.browser.viewport.width = 480
        settings.browser.viewport.height = 700
        await session.open(settings)
        try:
            await session.page.set_content(MARKUP)
            await session.page.evaluate(
                """() => {
                    const attach = Element.prototype.attachShadow;
                    Element.prototype.attachShadow = function(options) {
                        const shadow = attach.call(this, options);
                        if (this.hasAttribute('data-a11y-evidence-overlay'))
                            window.evidenceShadow = shadow;
                        return shadow;
                    };
                }"""
            )
            issue = finding("button-name", ["#hidden"])
            issue.title = (
                "Buttons must have discernible text: <img onerror=alert(1)> "
                "supplied-secret-fixture. Give every control a clear accessible name."
            )
            issue.occurrences.extend(
                finding("button-name", [selector]).occurrences[0]
                for selector in ("#bad", "#field", "#review")
            )
            duplicate_finding = finding("button-name", ["#bad"])
            shared_target = finding("target-size", ["#bad"])
            review = finding("button-name", ["#review"], "NEEDS_REVIEW")
            original = await session.page.content()
            screenshot = session.page.screenshot

            async def inspect_screenshot(**kwargs):
                overlay = await session.page.evaluate(
                    """() => ({
                        boxes:window.evidenceShadow.querySelectorAll('.box').length,
                        images:window.evidenceShadow.querySelectorAll('img').length,
                        labels:[...window.evidenceShadow.querySelectorAll('.label')].map(node => {
                            const r=node.getBoundingClientRect();
                            return {text:node.textContent,width:r.width,height:r.height,
                                left:r.left,right:r.right,top:r.top};
                        })
                    })"""
                )
                assert overlay["boxes"] == 2
                assert overlay["images"] == 0
                assert len(overlay["labels"]) == 2
                first = overlay["labels"][0]
                assert "#1 Buttons must have discernible text" in first["text"]
                assert "<img onerror=alert(1)> [REDACTED]" in first["text"]
                assert "#1 Finding target-size" in first["text"]
                assert "supplied-secret-fixture" not in first["text"]
                assert first["height"] > 50  # Full titles wrap, without ellipsis or clipping.
                assert all(0 <= node["left"] < node["right"] <= 480 for node in overlay["labels"])
                return await screenshot(**kwargs)

            monkeypatch.setattr(session.page, "screenshot", inspect_screenshot)
            await StateEvidenceCollector(
                tmp_path, Redactor(["supplied-secret-fixture"]), True
            ).capture(session, fixture_state(), [issue, duplicate_finding, shared_target, review])
            assert [node.screenshot_marker for node in issue.occurrences] == [None, 1, None, None]
            assert all(node.screenshot_note for node in issue.occurrences[2:])
            assert duplicate_finding.occurrences[0].screenshot_marker == 1
            assert shared_target.occurrences[0].screenshot_marker == 1
            assert review.occurrences[0].screenshot_marker == 2
            assert await session.page.content() == original
        finally:
            await session.close()

    asyncio.run(run())


def test_screenshot_failure_removes_temporary_highlights(tmp_path, monkeypatch):
    async def run():
        session = ChromiumSession()
        await session.open(load_settings(environ={"A11Y_URL": "https://example.test/"}))
        try:
            await session.page.set_content(MARKUP)
            original = await session.page.content()

            async def fail(**kwargs):
                assert await session.page.locator("[data-a11y-evidence-overlay]").count() == 1
                raise RuntimeError("screenshot fixture failure")

            monkeypatch.setattr(session.page, "screenshot", fail)
            state = fixture_state()
            issue = finding("button-name", ["#bad"])
            with pytest.raises(RuntimeError, match="screenshot fixture failure"):
                await StateEvidenceCollector(tmp_path, Redactor([]), True).capture(
                    session, state, [issue]
                )
            assert await session.page.content() == original
            assert state.screenshot is None
            assert issue.occurrences[0].screenshot_marker is None
        finally:
            await session.close()

    asyncio.run(run())


def test_highlights_remain_above_modal_and_align_on_scrolled_page(tmp_path):
    async def run():
        session = ChromiumSession()
        settings = load_settings(environ={"A11Y_URL": "https://example.test/"})
        settings.browser.viewport.width = 900
        settings.browser.viewport.height = 700
        await session.open(settings)
        try:
            await session.page.set_content(
                """<!doctype html><html><body style="height:1800px;margin:40px">
                <h1>Long page</h1><button id="lower" style="margin-top:1100px;
                width:200px;height:50px">Lower control</button>
                <dialog><button id="modal" style="width:200px;height:50px">
                Modal control</button></dialog>
                <script>document.querySelector('dialog').showModal();scrollTo(0,800)</script>
                </body></html>"""
            )
            original = await session.page.content()
            samples = await session.page.evaluate(
                """() => ['#lower','#modal'].map(selector => {
                    const r=document.querySelector(selector).getBoundingClientRect();
                    return [r.x+scrollX-3,r.y+scrollY+12];
                })"""
            )
            state = fixture_state()
            await StateEvidenceCollector(tmp_path, Redactor([]), True).capture(
                session, state, [finding("lower", ["#lower"]), finding("modal", ["#modal"])]
            )
            assert await session.page.content() == original
            encoded = base64.b64encode((tmp_path / state.screenshot).read_bytes()).decode()
            pixels = await session.page.evaluate(
                """async ({encoded,samples}) => {
                    const image=new Image();image.src='data:image/png;base64,'+encoded;
                    await image.decode();const canvas=document.createElement('canvas');
                    canvas.width=image.width;canvas.height=image.height;
                    const context=canvas.getContext('2d');context.drawImage(image,0,0);
                    return samples.map(([x,y]) => [...context.getImageData(x,y,1,1).data]);
                }""",
                {"encoded": encoded, "samples": samples},
            )
            assert pixels == [[190, 18, 60, 255]] * 2
        finally:
            await session.close()

    asyncio.run(run())


def test_visual_disabled_creates_no_highlights(tmp_path):
    class UnusedSession:
        @property
        def page(self):
            raise AssertionError("A disabled visual collector must not access the page")

    state = fixture_state()
    issue = finding("button-name", ["#bad"])
    evidence = asyncio.run(
        StateEvidenceCollector(tmp_path, Redactor([]), False).capture(
            UnusedSession(), state, [issue]
        )
    )
    assert len(evidence) == 1
    assert evidence[0].kind == "dom"
    assert state.screenshot is None
    assert issue.occurrences[0].screenshot_marker is None
