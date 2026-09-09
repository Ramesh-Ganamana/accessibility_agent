"""Post-login handoff uses real DOM controls without choosing application data for users."""

import asyncio
from dataclasses import asdict

from test_readiness import DOMSpyScanner, configured, page, readiness_server  # noqa: F401

from accessibility_agent.agent.orchestrator import ScanOrchestrator
from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.models import AuthenticationResult


class Authenticated:
    def __init__(self, script=""):
        self.script = script

    async def authenticate(self, session, credentials):
        if self.script:
            await session.page.evaluate(self.script)
        return AuthenticationResult(status="authenticated")


def settings_for(url, output, mode="interactive"):
    settings = configured(url, output, mode)
    settings.input_assistance.detection_wait_ms = 100
    settings.input_assistance.timeout_seconds = 5
    return settings


def modal_page():
    return page(
        '<dialog id="gate" aria-labelledby="gate-title">'
        '<h2 id="gate-title">Select domain</h2>'
        '<label for="domain">Domain</label><select id="domain" required>'
        '<option value="">Choose a domain</option><option value="north">North</option>'
        '</select><button type="button">Continue</button></dialog>',
        "document.querySelector('#gate').showModal();",
    )


def test_domain_choice_precedes_scan_and_survives_storage_replay(
    readiness_server,  # noqa: F811
    tmp_path,
):
    markup = page(
        '<a href="/accounts">Accounts</a><a href="/summary">Summary</a>'
        '<dialog id="gate" aria-labelledby="gate-title">'
        '<h2 id="gate-title">Select domain</h2>'
        '<label for="domain">Domain</label><select id="domain" required>'
        '<option value="">Choose a domain</option><option value="north">North</option>'
        '</select><label for="note">Account note</label>'
        '<input id="note" value="private-prefilled-account-note" readonly>'
        '<button type="button" onclick="finishChoice()">Continue</button></dialog>',
        """
        const gate = document.querySelector('#gate');
        if (localStorage.getItem('domain') && sessionStorage.getItem('workspace')) {
            gate.remove();
        } else {
            gate.showModal();
        }
        function finishChoice() {
            localStorage.setItem('domain', document.querySelector('#domain').value);
            sessionStorage.setItem('workspace', 'chosen-workspace');
            gate.close();
            gate.remove();
        }
        """,
    )
    url = readiness_server(
        markup,
        {"/accounts": (0, "text/html", markup), "/summary": (0, "text/html", markup)},
    )
    settings = settings_for(url, tmp_path)

    class TrackingSession(ChromiumSession):
        restores = 0
        checkpoints = 0

        async def checkpoint(self):
            assert await self.page.locator("#gate").count() == 0
            self.checkpoints += 1
            await super().checkpoint()

        async def restore(self):
            self.restores += 1
            await super().restore()

    class StorageScanner(DOMSpyScanner):
        async def scan(self, session, state):
            assert await session.page.locator("#gate").count() == 0
            assert await session.page.evaluate("localStorage.getItem('domain')") == "north"
            assert (
                await session.page.evaluate("sessionStorage.getItem('workspace')")
                == "chosen-workspace"
            )
            return await super().scan(session, state)

    session = TrackingSession()
    scanner = StorageScanner()
    prompts = []

    async def complete_input(blockers):
        assert not scanner.observations
        assert not session._crawl_mode
        assert session.checkpoints == 0
        prompts.extend(blockers)
        assert any("Select domain" in blocker.title for blocker in blockers)
        fields = [field for blocker in blockers for field in blocker.fields]
        assert any(field.label == "Domain" and field.required for field in fields)
        assert any("North" in str(field.options) for field in fields)
        metadata = repr([asdict(blocker) for blocker in blockers])
        assert "private-prefilled-account-note" not in metadata
        assert all("value" not in asdict(field) for field in fields)
        await session.page.locator("#domain").select_option("north")
        await session.page.get_by_role("button", name="Continue").click()
        return True

    report = asyncio.run(
        ScanOrchestrator(
            session=session,
            authenticator=Authenticated(),
            scanner=scanner,
            input_handler=complete_input,
        ).run(settings)
    )
    assert prompts
    assert report.authentication.status == "authenticated"
    assert report.coverage.urls_scanned == 3
    assert {item["url"] for item in scanner.observations} == {"/", "/accounts", "/summary"}
    assert session.restores >= 2
    assert session.checkpoints >= 1
    assert not any(area.reason.startswith("user_input_") for area in report.unscanned)


def test_delayed_and_chained_dialogs_require_completion_before_scan(
    readiness_server,  # noqa: F811
    tmp_path,
):
    url = readiness_server(
        page(
            '<dialog id="first" aria-label="Select organization">'
            '<label for="organization">Organization</label><select id="organization" required>'
            '<option value="">Choose</option><option value="north">North</option>'
            '</select></dialog><dialog id="second" aria-label="Select workspace">'
            '<label for="workspace">Workspace</label><input id="workspace" required></dialog>'
        )
    )
    settings = settings_for(url, tmp_path)
    session = ChromiumSession()
    scanner = DOMSpyScanner()
    prompts = []

    async def complete_input(blockers):
        assert not scanner.observations
        prompts.append([blocker.title for blocker in blockers])
        if len(prompts) == 1:
            assert any("Select organization" in blocker.title for blocker in blockers)
            await session.page.locator("#organization").select_option("north")
            await session.page.evaluate(
                """() => {
                    document.querySelector('#first').remove();
                    setTimeout(() => document.querySelector('#second').showModal(), 50);
                }"""
            )
        else:
            assert any("Select workspace" in blocker.title for blocker in blockers)
            await session.page.locator("#workspace").fill("North workspace")
            await session.page.evaluate(
                """() => {
                    document.querySelector('#second').remove();
                    document.body.dataset.phase = 'ready';
                }"""
            )
        return True

    report = asyncio.run(
        ScanOrchestrator(
            session=session,
            authenticator=Authenticated(
                "() => setTimeout(() => document.querySelector('#first').showModal(), 50)"
            ),
            scanner=scanner,
            input_handler=complete_input,
        ).run(settings)
    )
    assert len(prompts) == 2
    assert report.coverage.states_scanned == 1
    assert scanner.observations[0]["phase"] == "ready"


def test_headless_blocker_without_handler_is_reported_untested(
    readiness_server,  # noqa: F811
    tmp_path,
):
    url = readiness_server(modal_page())
    settings = settings_for(url, tmp_path)
    assert settings.browser.headless
    scanner = DOMSpyScanner()
    report = asyncio.run(
        ScanOrchestrator(authenticator=Authenticated(), scanner=scanner).run(settings)
    )
    assert not scanner.observations
    assert report.coverage.states_scanned == 0
    assert report.authentication.status == "authenticated"
    assert report.scan.status == "failed"
    assert any(area.reason == "user_input_required" for area in report.unscanned)


def test_cancelled_handoff_does_not_scan_the_blocked_page(
    readiness_server,  # noqa: F811
    tmp_path,
):
    url = readiness_server(modal_page())
    scanner = DOMSpyScanner()
    prompts = []

    async def cancel(blockers):
        prompts.extend(blockers)
        return False

    report = asyncio.run(
        ScanOrchestrator(authenticator=Authenticated(), scanner=scanner, input_handler=cancel).run(
            settings_for(url, tmp_path)
        )
    )
    assert prompts
    assert not scanner.observations
    assert report.coverage.states_scanned == 0
    assert any(area.reason == "user_input_cancelled" for area in report.unscanned)


def test_handoff_timeout_cancels_wait_and_reports_page_untested(
    readiness_server,  # noqa: F811
    tmp_path,
):
    url = readiness_server(modal_page())
    settings = settings_for(url, tmp_path)
    settings.input_assistance.timeout_seconds = 1
    scanner = DOMSpyScanner()
    cancelled = []

    async def wait_for_user(blockers):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
        return True

    report = asyncio.run(
        ScanOrchestrator(
            authenticator=Authenticated(), scanner=scanner, input_handler=wait_for_user
        ).run(settings)
    )
    assert cancelled
    assert not scanner.observations
    assert report.coverage.states_scanned == 0
    assert any(area.reason == "user_input_timeout" for area in report.unscanned)
    assert not any(area.reason == "duration_budget" for area in report.unscanned)


def test_ordinary_search_and_hidden_dialog_do_not_prompt(readiness_server, tmp_path):  # noqa: F811
    url = readiness_server(
        page(
            '<form role="search"><label for="search">Search</label>'
            '<input id="search" type="search" required><button>Search</button></form>'
            '<p>Account overview</p><dialog aria-label="Hidden preferences">'
            '<label for="hidden-input">Preference</label><input id="hidden-input" required>'
            "</dialog>"
        )
    )
    prompts = []

    async def unexpected_prompt(blockers):
        prompts.extend(blockers)
        return False

    scanner = DOMSpyScanner()
    report = asyncio.run(
        ScanOrchestrator(
            authenticator=Authenticated(), scanner=scanner, input_handler=unexpected_prompt
        ).run(settings_for(url, tmp_path, "links"))
    )
    assert not prompts
    assert report.coverage.states_scanned == 1
    assert len(scanner.observations) == 1


def test_configured_blocking_region_exposes_required_field_and_resumes(
    readiness_server,  # noqa: F811
    tmp_path,
):
    url = readiness_server(
        page(
            '<section class="workspace-gate" aria-label="Choose region">'
            '<label for="region">Region</label><input id="region" required></section>'
            "<p>Application overview</p>"
        )
    )
    settings = settings_for(url, tmp_path)
    settings.input_assistance.blocking_selectors = [".workspace-gate"]
    session = ChromiumSession()
    scanner = DOMSpyScanner()
    prompts = []

    async def fill_region(blockers):
        assert not scanner.observations
        prompts.extend(blockers)
        fields = [field for blocker in blockers for field in blocker.fields]
        assert any(field.label == "Region" and field.required for field in fields)
        await session.page.locator("#region").fill("North")
        await session.page.locator(".workspace-gate").evaluate("element => element.remove()")
        return True

    report = asyncio.run(
        ScanOrchestrator(
            session=session,
            authenticator=Authenticated(),
            scanner=scanner,
            input_handler=fill_region,
        ).run(settings)
    )
    assert prompts
    assert report.coverage.states_scanned == 1
    assert len(scanner.observations) == 1


def test_public_required_form_does_not_pause_crawl(readiness_server, tmp_path):  # noqa: F811
    url = readiness_server(
        page(
            '<p>Contact our public team.</p><form><label>Email address '
            '<input type="email" required></label><button>Send</button></form>'
        )
    )
    prompts = []

    async def unexpected_prompt(blockers):
        prompts.extend(blockers)
        return False

    scanner = DOMSpyScanner()
    report = asyncio.run(
        ScanOrchestrator(scanner=scanner, input_handler=unexpected_prompt).run(
            settings_for(url, tmp_path, "links")
        )
    )
    assert report.authentication.status == "not_required"
    assert not prompts
    assert report.coverage.states_scanned == 1


def test_blocker_on_later_url_pauses_before_that_page_is_scanned(
    readiness_server,  # noqa: F811
    tmp_path,
):
    gated = page(
        '<dialog id="gate" aria-label="Choose workspace"><label>Workspace '
        '<select required><option value="">Choose</option><option value="north">North</option>'
        '</select></label><button type="button" onclick="finishSetup()">Continue</button>'
        "</dialog>",
        """
        document.querySelector('#gate').showModal();
        function finishSetup() {
            localStorage.setItem('workspace', 'north');
            document.querySelector('#gate').remove();
        }
        """,
    )
    url = readiness_server(
        page('<a href="/workspace">Open workspace</a>'),
        {"/workspace": (0, "text/html", gated)},
    )
    settings = settings_for(url, tmp_path, "links")
    session = ChromiumSession()
    scanner = DOMSpyScanner()
    prompts = []

    async def choose_workspace(blockers):
        assert [item["url"] for item in scanner.observations] == ["/"]
        assert session.page.url.endswith("/workspace")
        prompts.extend(blockers)
        await session.page.locator("select").select_option("north")
        await session.page.get_by_role("button", name="Continue").click()
        return True

    report = asyncio.run(
        ScanOrchestrator(
            session=session,
            authenticator=Authenticated(),
            scanner=scanner,
            input_handler=choose_workspace,
        ).run(settings)
    )
    assert len(prompts) == 1
    assert report.coverage.states_scanned == 2
    assert [item["url"] for item in scanner.observations] == ["/", "/workspace"]
    assert not any("Choose workspace" in item["text"] for item in scanner.observations)

    unattended_scanner = DOMSpyScanner()
    unattended = asyncio.run(
        ScanOrchestrator(
            authenticator=Authenticated(), scanner=unattended_scanner
        ).run(settings_for(url, tmp_path / "unattended", "links"))
    )
    assert unattended.coverage.states_scanned == 1
    assert [item["url"] for item in unattended_scanner.observations] == ["/"]
    required = [area for area in unattended.unscanned if area.reason == "user_input_required"]
    assert len(required) == 1
    assert required[0].url.endswith("/workspace")


def test_same_url_action_gate_pauses_after_ui_settles(readiness_server, tmp_path):  # noqa: F811
    url = readiness_server(
        page(
            '<button type="button" onclick="document.querySelector(\'#gate\').showModal()">'
            'Open workspace setup</button><dialog id="gate" aria-label="Select tenant">'
            '<label>Tenant <select required><option value="">Choose</option>'
            '<option value="north">North</option></select></label>'
            '<button type="button" onclick="document.querySelector(\'#gate\').remove()">'
            "Continue</button></dialog>"
        )
    )
    settings = settings_for(url, tmp_path)
    session = ChromiumSession()
    scanner = DOMSpyScanner()
    prompts = []

    async def choose_tenant(blockers):
        assert len(scanner.observations) == 1
        prompts.extend(blockers)
        assert blockers[0].title == "Select tenant"
        await session.page.locator("select").select_option("north")
        await session.page.get_by_role("button", name="Continue").click()
        return True

    report = asyncio.run(
        ScanOrchestrator(
            session=session,
            authenticator=Authenticated(),
            scanner=scanner,
            input_handler=choose_tenant,
        ).run(settings)
    )
    assert len(prompts) == 1
    assert report.coverage.states_scanned >= 2
    assert all("Select tenant" not in item["text"] for item in scanner.observations)
