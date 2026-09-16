import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from accessibility_agent.accessibility.axe_scanner import AxeScanner
from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.config.settings import load_settings
from accessibility_agent.models import State
from accessibility_agent.utils.privacy import Redactor


@pytest.fixture
def frame_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(("GET", self.path))
            if self.path == "/redirect-frame":
                self.send_response(302)
                self.send_header(
                    "Location", f"http://localhost:{self.server.server_port}/external-child"
                )
                self.end_headers()
                return
            if self.path == "/":
                markup = f"""<!doctype html><html lang="en"><title>Frame scan</title>
                <main><h1>Frame scan</h1>
                <iframe id="same" title="Same origin" src="/child"></iframe>
                <iframe id="inline" title="Inline" srcdoc="&lt;input id='inline-input'&gt;">
                </iframe>
                <iframe id="blank" title="Inherited blank"></iframe>
                <iframe id="opaque" sandbox title="Opaque"
                    srcdoc="&lt;button id='opaque-button'&gt;&lt;/button&gt;"></iframe>
                <iframe id="external" title="External"
                    src="http://localhost:{self.server.server_port}/external-child"></iframe>
                <iframe id="redirect" title="Redirect" src="/redirect-frame"></iframe>
                <iframe id="risky" title="Risky" src="/delete"></iframe>
                </main><script>
                document.querySelector('#blank').contentDocument.body.innerHTML =
                    '<button id="blank-button"></button>';
                </script></html>"""
            elif self.path == "/child":
                markup = """<!doctype html><html lang="en"><title>Child</title><main>
                <img id="child-image" src="/image">
                <iframe id="nested" title="Nested"
                    srcdoc="&lt;button id='nested-button'&gt;&lt;/button&gt;"></iframe>
                </main></html>"""
            else:
                markup = "<!doctype html><html lang='en'><title>Empty</title></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(markup.encode())

        def do_POST(self):
            requests.append(("POST", self.path))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize("same_origin_only", [True, False])
def test_readable_frames_are_loaded_and_scanned_with_scoped_targets(frame_server, same_origin_only):
    url, requests = frame_server

    async def run():
        settings = load_settings(environ={"A11Y_URL": url})
        settings.crawl.timeout = 5000
        settings.crawl.same_origin_only = same_origin_only
        session = ChromiumSession()
        await session.open(settings)
        try:
            session.enable_crawl_policy()
            await session.navigate(url)
            await session.page.wait_for_load_state("load")
            assert await session.page.frame_locator("#same").locator("#child-image").count() == 1
            state = State(state_id="frame-state", url=url, title="Frame scan", dom_hash="frames")
            result = await AxeScanner(settings.accessibility, Redactor([]), 10000).scan(
                session, state
            )
            targets = {
                (finding.rule_id, tuple(occurrence.target))
                for finding in result.findings
                for occurrence in finding.occurrences
                if all(isinstance(part, str) for part in occurrence.target)
            }
            assert ("image-alt", ("#same", "#child-image")) in targets
            assert ("label", ("#inline", "#inline-input")) in targets
            assert ("button-name", ("#blank", "#blank-button")) in targets
            assert ("button-name", ("#same", "#nested", "#nested-button")) in targets
            assert not any("#opaque-button" in target for _, target in targets)
            assert any(
                rule.rule_id == "frame-tested" and rule.outcome == "incomplete"
                for rule in result.rules
            )
            assert ("GET", "/child") in requests
            assert ("GET", "/redirect-frame") in requests
            assert ("GET", "/external-child") not in requests
            assert ("GET", "/delete") not in requests
            assert session.blocked_requests >= 3
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["interactive", "links"])
def test_child_navigation_does_not_allow_mutating_requests(frame_server, mode):
    url, requests = frame_server

    async def run():
        settings = load_settings(environ={"A11Y_URL": url})
        settings.crawl.mode = mode
        settings.crawl.timeout = 5000
        # The former frame guard denied these navigations even when allowlisted.
        settings.crawl.allowed_request_urls = [url + "submit-frame"]
        session = ChromiumSession()
        await session.open(settings)
        try:
            session.enable_crawl_policy()
            await session.navigate(url + "child")
            async with session.page.expect_event(
                "requestfailed", predicate=lambda request: request.url == url + "submit-frame"
            ):
                await session.page.evaluate("""() => {
                    const frame = document.createElement('iframe');
                    frame.name = 'submission';
                    document.body.append(frame);
                    const form = document.createElement('form');
                    form.method = 'POST'; form.action = '/submit-frame'; form.target = frame.name;
                    document.body.append(form); form.submit();
                }""")
            assert ("POST", "/submit-frame") not in requests
            assert session.blocked_requests >= 1
        finally:
            await session.close()

    asyncio.run(run())
