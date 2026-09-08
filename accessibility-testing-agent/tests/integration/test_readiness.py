"""Readiness regressions use real browser timing and inspect the DOM when scanning starts."""

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from accessibility_agent.agent.orchestrator import ScanOrchestrator
from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.config.settings import load_settings
from accessibility_agent.models import ScanResult


@pytest.fixture
def readiness_server():
    servers = []

    def start(markup, resources=None):
        routes = {"/": (0, "text/html", markup), **(resources or {})}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                delay, content_type, body = routes.get(
                    urlsplit(self.path).path, (0, "text/plain", "Not found")
                )
                time.sleep(delay)
                payload = body.encode() if isinstance(body, str) else body
                self.send_response(200 if urlsplit(self.path).path in routes else 404)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # A readiness timeout can close a still-loading test page.

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}/"

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def page(body, script=""):
    return (
        '<!doctype html><html lang="en"><head><title>Readiness fixture</title></head>'
        f"<body><main><h1>Application</h1>{body}</main><script>{script}</script></body></html>"
    )


def configured(url, output, mode):
    settings = load_settings(environ={"A11Y_URL": url})
    settings.output_dir = output
    settings.crawl.mode = mode
    settings.crawl.timeout = 10000
    settings.crawl.settle_ms = 20
    settings.crawl.dom_quiet_ms = 150
    settings.crawl.network_idle_ms = 100
    settings.crawl.stability_timeout_ms = 3000
    settings.crawl.max_states = 5
    settings.crawl.max_actions = 5
    settings.crawl.max_duration_seconds = 30
    settings.visual.enabled = False
    return settings


class DOMSpyScanner:
    def __init__(self):
        self.observations = []

    async def scan(self, session, state):
        self.observations.append(
            await session.page.evaluate(
                """() => ({
                    url: location.pathname,
                    text: document.body.innerText,
                    phase: document.body.dataset.phase || '',
                    complete: document.readyState === 'complete',
                    imagesComplete: [...document.images].every(image => image.complete),
                    color: getComputedStyle(document.querySelector('main')).color,
                    ready: !!document.querySelector('#application-ready'),
                    loader: !!document.querySelector('.custom-pending')
                })"""
            )
        )
        return ScanResult(state_id=state.state_id, engine="dom-spy", engine_version="1")


def run_scan(settings):
    scanner = DOMSpyScanner()
    report = asyncio.run(ScanOrchestrator(session=ChromiumSession(), scanner=scanner).run(settings))
    return report, scanner.observations


@pytest.mark.parametrize("mode", ["interactive", "links"])
def test_waits_for_data_request_started_after_a_quiet_shell(readiness_server, tmp_path, mode):
    url = readiness_server(
        page(
            '<section id="results"></section>',
            """
            setTimeout(async () => {
                const response = await fetch('/records');
                document.querySelector('#results').textContent = await response.text();
                document.body.dataset.phase = 'ready';
            }, 70);
            """,
        ),
        {"/records": (0.55, "text/plain", "Customer records are available")},
    )
    report, observations = run_scan(configured(url, tmp_path, mode))
    assert report.coverage.states_scanned == 1
    assert len(observations) == 1
    assert observations[0]["phase"] == "ready"
    assert "Customer records are available" in observations[0]["text"]


@pytest.mark.parametrize("mode", ["interactive", "links"])
def test_waits_for_dom_work_scheduled_by_response_handler(readiness_server, tmp_path, mode):
    url = readiness_server(
        page(
            '<section id="results"></section>',
            """
            fetch('/records').then(response => response.text()).then(text => {
                document.querySelector('#results').textContent = 'First render';
                setTimeout(() => {
                    document.querySelector('#results').textContent = 'Second render';
                    setTimeout(() => {
                        document.querySelector('#results').textContent = text;
                        document.body.dataset.phase = 'ready';
                    }, 110);
                }, 110);
            });
            """,
        ),
        {"/records": (0.4, "text/plain", "Fully rendered records")},
    )
    report, observations = run_scan(configured(url, tmp_path, mode))
    assert report.coverage.states_scanned == 1
    assert observations[0]["phase"] == "ready"
    assert "Fully rendered records" in observations[0]["text"]


@pytest.mark.parametrize("mode", ["interactive", "links"])
@pytest.mark.parametrize("resource", ["image", "stylesheet"])
def test_waits_for_resources_added_after_document_load(readiness_server, tmp_path, mode, resource):
    if resource == "image":
        script = """
            const element = document.createElement('img');
            element.alt = 'Customer chart';
            element.src = '/delayed.svg';
        """
        resources = {
            "/delayed.svg": (
                0.55,
                "image/svg+xml",
                '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">'
                '<rect width="120" height="80" fill="blue"/></svg>',
            )
        }
    else:
        script = """
            const element = document.createElement('link');
            element.rel = 'stylesheet';
            element.href = '/delayed.css';
        """
        resources = {"/delayed.css": (0.55, "text/css", "main { color: rgb(12, 34, 56); }")}
    url = readiness_server(
        page(
            "<p>Application content</p>",
            "window.addEventListener('load', () => setTimeout(() => {"
            + script
            + """
                element.onload = () => { document.body.dataset.phase = 'ready'; };
                document.querySelector('main').append(element);
            }, 30));
            """,
        ),
        resources,
    )
    report, observations = run_scan(configured(url, tmp_path, mode))
    assert report.coverage.states_scanned == 1
    assert observations[0]["complete"]
    assert observations[0]["phase"] == "ready"
    assert observations[0]["imagesComplete"]
    if resource == "stylesheet":
        assert observations[0]["color"] == "rgb(12, 34, 56)"


@pytest.mark.parametrize("mode", ["interactive", "links"])
def test_waits_for_custom_ready_selector_and_loader(readiness_server, tmp_path, mode):
    url = readiness_server(
        page(
            '<span class="custom-pending">Preparing account</span>',
            """
            setTimeout(() => {
                document.querySelector('main').insertAdjacentHTML(
                    'beforeend', '<section id="application-ready">Account details</section>');
            }, 400);
            setTimeout(() => {
                document.querySelector('.custom-pending').remove();
                document.body.dataset.phase = 'ready';
            }, 750);
            """,
        )
    )
    settings = configured(url, tmp_path, mode)
    settings.crawl.ready_selector = "#application-ready"
    settings.crawl.loading_selectors = [".custom-pending"]
    report, observations = run_scan(settings)
    assert report.coverage.states_scanned == 1
    assert observations[0]["ready"]
    assert not observations[0]["loader"]
    assert observations[0]["phase"] == "ready"


@pytest.mark.parametrize("mode", ["interactive", "links"])
@pytest.mark.parametrize("blocker", ["loader", "ready_selector"])
def test_incomplete_page_is_untested_instead_of_scanned(readiness_server, tmp_path, mode, blocker):
    body = (
        '<section aria-busy="true">Loading account details...</section>'
        if blocker == "loader"
        else "<p>Account shell</p>"
    )
    url = readiness_server(page(body))
    settings = configured(url, tmp_path, mode)
    settings.crawl.stability_timeout_ms = 650
    if blocker == "ready_selector":
        settings.crawl.ready_selector = "#application-ready"
    report, observations = run_scan(settings)
    assert observations == []
    assert report.coverage.states_scanned == 0
    assert report.coverage.urls_scanned == 0
    assert report.scan.status == "failed"
    assert any(area.reason == "page_readiness_timeout" for area in report.unscanned)


@pytest.mark.parametrize("mode", ["interactive", "links"])
def test_waits_after_following_a_discovered_url(readiness_server, tmp_path, mode):
    next_page = page(
        '<section id="results"></section>',
        """
        fetch('/records').then(response => response.text()).then(text => {
            document.querySelector('#results').textContent = text;
            document.body.dataset.phase = 'ready';
        });
        """,
    )
    url = readiness_server(
        page('<a href="/accounts">Accounts</a>'),
        {
            "/accounts": (0, "text/html", next_page),
            "/records": (0.55, "text/plain", "All customer accounts"),
        },
    )
    report, observations = run_scan(configured(url, tmp_path, mode))
    assert report.coverage.urls_scanned == 2
    account_observations = [item for item in observations if item["url"] == "/accounts"]
    assert len(account_observations) == 1
    assert account_observations[0]["phase"] == "ready"
    assert "All customer accounts" in account_observations[0]["text"]


def test_waits_again_after_using_a_control(readiness_server, tmp_path):
    url = readiness_server(
        page(
            '<button type="button" onclick="showDetails(this)">Show details</button>'
            '<section id="results"></section>',
            """
            async function showDetails(button) {
                button.hidden = true;
                const response = await fetch('/details');
                const text = await response.text();
                document.querySelector('#results').textContent = 'First details render';
                setTimeout(() => {
                    document.querySelector('#results').textContent = text;
                    document.body.dataset.phase = 'ready';
                }, 110);
            }
            """,
        ),
        {"/details": (0.5, "text/plain", "Complete account details")},
    )
    report, observations = run_scan(configured(url, tmp_path, "interactive"))
    assert report.coverage.interactive_elements_tested >= 1
    assert len(observations) == 2
    assert observations[-1]["phase"] == "ready"
    assert "Complete account details" in observations[-1]["text"]
    assert not any("First details render" in item["text"] for item in observations)
