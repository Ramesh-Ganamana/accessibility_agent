"""Resilient Phase 1 execution. Failures produce explicit partial/failed reports."""

import asyncio
from contextlib import aclosing
from datetime import UTC, datetime
from uuid import uuid4

from accessibility_agent.accessibility.axe_scanner import AxeScanner
from accessibility_agent.authentication.authenticator import SemanticAuthenticator
from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.config.settings import Settings
from accessibility_agent.crawler.crawler import InitialCrawler
from accessibility_agent.crawler.interactive import InteractiveCrawler
from accessibility_agent.crawler.readiness import ReadinessTimeout, state_is_current
from accessibility_agent.evidence.evidence_manager import StateEvidenceCollector
from accessibility_agent.findings.repository import MemoryFindingRepository
from accessibility_agent.interaction.coordinator import (
    InputAssistanceRequired,
    InputCoordinator,
    InputHandler,
)
from accessibility_agent.interfaces import AccessibilityScanner, Authenticator, BrowserSession
from accessibility_agent.models import (
    ApplicationInfo,
    AuthenticationResult,
    Coverage,
    Impact,
    Report,
    ScanMetadata,
    UnscannedArea,
)
from accessibility_agent.reporting.writers import HTMLReportWriter, JSONReportWriter
from accessibility_agent.utils.logging import event
from accessibility_agent.utils.privacy import Redactor


class ScanOrchestrator:
    def __init__(
        self,
        *,
        session: BrowserSession | None = None,
        authenticator: Authenticator | None = None,
        scanner: AccessibilityScanner | None = None,
        input_handler: InputHandler | None = None,
    ) -> None:
        self.session = session
        self.authenticator = authenticator or SemanticAuthenticator()
        self.scanner = scanner
        self.input_handler = input_handler

    async def run(self, settings: Settings) -> Report:
        redactor = Redactor(
            [
                settings.authentication.username.get_secret_value(),
                settings.authentication.password.get_secret_value(),
                *(value.get_secret_value() for value in settings.crawl.form_values.values()),
            ]
        )
        report = Report(
            application=ApplicationInfo(url=redactor.url(settings.application.url)),
            scan=ScanMetadata(
                scan_id=uuid4().hex,
                started_at=datetime.now(UTC),
                status="running",
                wcag=f"{settings.accessibility.version}-{settings.accessibility.level}",
            ),
            configuration={
                "browser": "chromium",
                "crawl_mode": settings.crawl.mode,
                "test_environment": settings.crawl.test_environment,
                "headless": settings.browser.headless,
                "max_states": settings.crawl.max_states,
                "max_depth": settings.crawl.max_depth,
                "max_actions": settings.crawl.max_actions,
                "max_replay_steps": settings.crawl.max_replay_steps,
                "same_origin_only": settings.crawl.same_origin_only,
                "authentication_timeout_ms": settings.authentication.timeout,
                "timeout_ms": settings.crawl.timeout,
                "stability_timeout_ms": settings.crawl.stability_timeout_ms,
                "dom_quiet_ms": settings.crawl.dom_quiet_ms,
                "network_idle_ms": settings.crawl.network_idle_ms,
                "max_duration_seconds": settings.crawl.max_duration_seconds,
                "visual_enabled": settings.visual.enabled,
                "ai_requested": settings.ai.enabled,
                "ai_executed": False,
                "keyboard_executed": False,
                "input_assistance_enabled": settings.input_assistance.enabled,
            },
            limitations=[
                "Interactive mode crawls safe URLs directly and explores UI states through "
                "verified replay. Links mode "
                "retains the original navigation-only behavior.",
                "Keyboard, browser accessibility tree and contextual AI analysis "
                "are not implemented.",
                "Frames are excluded from axe scans; frame/shadow navigation is unsupported.",
                "One safe popup URL per interaction is tested through the existing navigator. "
                "Additional popups and inline about:blank windows are recorded as untested. "
                "Lazy discovery uses up to three scroll steps for the document and four "
                "visible scroll panels per browser page/URL.",
                "DOM fingerprints include visible text; dynamic timestamps may create different "
                "states.",
                "Screenshots mask inputs, private regions and supplied credentials; other page "
                "data may remain.",
                "Use authorized test accounts. Link safety is heuristic and cannot prove "
                "server-side effects.",
                "Replay restores in-memory browser storage, not server-side data. Non-safe "
                "allowed actions are never replayed; further safe URLs may be visited "
                "independently.",
                "Form submission and unknown actions are skipped by default. Unsafe HTTP "
                "methods require explicit allowed_request_urls in interactive mode.",
                "Opt-in testing mode permits unfamiliar clickable controls and their replay, "
                "but does not disable destructive-action, submission, origin or request guards. "
                "Click handlers may still have side effects; use authorized test data.",
            ],
        )
        session = self.session or ChromiumSession()
        input_coordinator = InputCoordinator(settings, redactor, self.input_handler)

        async def prepare_page(active_session: BrowserSession, watch_delayed: bool = True) -> bool:
            return await input_coordinator.prepare(
                active_session,
                include_setup_forms=(
                    report.authentication is not None
                    and report.authentication.status == "authenticated"
                ),
                watch_delayed=watch_delayed,
            )

        crawler = (
            InteractiveCrawler(settings, redactor, prepare_page)
            if settings.crawl.mode == "interactive"
            else InitialCrawler(settings, redactor, prepare_page)
        )
        repository = MemoryFindingRepository()
        evidence = StateEvidenceCollector(settings.output_dir, redactor, settings.visual.enabled)
        runtime_failure = False
        event("scan_started")
        try:
            async with asyncio.timeout(settings.crawl.max_duration_seconds):
                scanner = self.scanner or AxeScanner(
                    settings.accessibility, redactor, settings.crawl.timeout
                )
                await session.open(settings)
                event("browser_initialized")
                response = await session.page.goto(
                    settings.application.url, wait_until="domcontentloaded"
                )
                if response is not None and response.status >= 400:
                    raise RuntimeError("entry_http_error")
                try:
                    report.authentication = await self.authenticator.authenticate(
                        session,
                        settings.authentication.model_copy(
                            update={
                                "allowed_origins": [
                                    *settings.authentication.allowed_origins,
                                    settings.application.url,
                                ]
                            }
                        ),
                    )
                except Exception:
                    report.authentication = AuthenticationResult(
                        status="failed", reason="login_not_confirmed_timeout_or_error"
                    )
                if session.blocked_auth_origin:
                    report.authentication = AuthenticationResult(
                        status="blocked", reason="authentication_origin_not_trusted"
                    )
                if report.authentication.status in {"blocked", "failed"}:
                    report.unscanned.append(
                        UnscannedArea(
                            url=report.application.url,
                            reason=report.authentication.reason,
                            detail=(
                                f"Sign-in requires {session.blocked_auth_origin}. "
                                "Configure authentication.allowed_origins or --auth-origin."
                                if session.blocked_auth_origin
                                else ""
                            ),
                        )
                    )
                    runtime_failure = True
                else:
                    event("authentication_" + report.authentication.status)
                    await prepare_page(session)
                    session.enable_crawl_policy()
                    report.application.title = redactor.text(await session.page.title())
                    async with aclosing(crawler.discover(session)) as discovered:
                        async for state in discovered:
                            event("state_discovered", state_id=state.state_id)
                            try:
                                if not await state_is_current(
                                    session.page,
                                    settings.crawl,
                                    state.state_id,
                                    bool(crawler.readiness_fallbacks.get(state.state_id)),
                                ):
                                    state.status = "failed"
                                    runtime_failure = True
                                    report.unscanned.append(
                                        UnscannedArea(
                                            url=state.url,
                                            state_id=state.state_id,
                                            reason="page_changed_before_scan",
                                            detail=(
                                                "Page resumed loading after discovery; "
                                                "scan skipped."
                                            ),
                                            retryable=True,
                                        )
                                    )
                                    continue
                                result = await scanner.scan(session, state)
                                if not await state_is_current(
                                    session.page,
                                    settings.crawl,
                                    state.state_id,
                                    bool(crawler.readiness_fallbacks.get(state.state_id)),
                                ):
                                    state.status = "failed"
                                    runtime_failure = True
                                    report.unscanned.append(
                                        UnscannedArea(
                                            url=state.url,
                                            state_id=state.state_id,
                                            reason="page_changed_during_scan",
                                            detail=(
                                                "UI or network activity changed during "
                                                "accessibility checks; results discarded."
                                            ),
                                            retryable=True,
                                        )
                                    )
                                    continue
                                state.status = "scanned"
                                report.results.append(result)
                                try:
                                    collected = await evidence.capture(
                                        session, state, result.findings
                                    )
                                    report.evidence.extend(collected)
                                    ids = [item.evidence_id for item in collected]
                                    for rule in result.rules:
                                        for node in rule.nodes:
                                            node.evidence = ids
                                    for finding in result.findings:
                                        for occurrence in finding.occurrences:
                                            occurrence.evidence = ids
                                except Exception as error:
                                    runtime_failure = True
                                    report.unscanned.append(
                                        UnscannedArea(
                                            url=state.url,
                                            state_id=state.state_id,
                                            reason="evidence_" + type(error).__name__,
                                            retryable=True,
                                        )
                                    )
                                repository.add(result.findings)
                                event("state_scanned", state_id=state.state_id)
                            except Exception as error:
                                state.status = "failed"
                                runtime_failure = True
                                report.unscanned.append(
                                    UnscannedArea(
                                        url=state.url,
                                        state_id=state.state_id,
                                        reason="scanner_" + type(error).__name__,
                                        retryable=True,
                                    )
                                )
                                event("state_scan_failed", state_id=state.state_id)

        except InputAssistanceRequired as error:
            runtime_failure = True
            report.unscanned.append(
                UnscannedArea(
                    url=redactor.url(session.page.url),
                    reason=error.reason,
                    detail=redactor.text(error.detail),
                    retryable=True,
                )
            )
            event(error.reason)
        except ReadinessTimeout as error:
            runtime_failure = True
            report.unscanned.append(
                UnscannedArea(
                    url=report.application.url,
                    reason="page_readiness_timeout",
                    detail=", ".join(error.reasons),
                    retryable=True,
                )
            )
        except TimeoutError:
            runtime_failure = True
            report.unscanned.append(
                UnscannedArea(url=report.application.url, reason="duration_budget")
            )
        except Exception as error:
            runtime_failure = True
            auth_blocked = session.blocked_auth_origin
            if auth_blocked:
                report.authentication = AuthenticationResult(
                    status="blocked", reason="authentication_origin_not_trusted"
                )
            report.unscanned.append(
                UnscannedArea(
                    url=report.application.url,
                    reason="authentication_origin_not_trusted"
                    if auth_blocked
                    else "scan_" + type(error).__name__,
                    detail=(
                        f"Sign-in requires {auth_blocked}. "
                        "Configure authentication.allowed_origins or --auth-origin."
                        if auth_blocked
                        else ""
                    ),
                )
            )
            event("scan_failed")
        finally:
            try:
                await session.close()
            except Exception:
                runtime_failure = True
                report.unscanned.append(
                    UnscannedArea(url=report.application.url, reason="browser_cleanup_failed")
                )
        for action in crawler.actions:
            if action.outcome == "pending":
                action.outcome, action.reason = "skipped", "scan_interrupted"
                report.unscanned.append(
                    UnscannedArea(
                        url=report.application.url,
                        action_id=action.action_id,
                        reason="scan_interrupted",
                    )
                )
        for state in crawler.states:
            if state.status == "discovered":
                state.status = "failed"
        report.graph = crawler.graph()
        report.unscanned.extend(crawler.unscanned())
        if crawler.readiness_fallbacks:
            report.limitations.append(
                f"{len(crawler.readiness_fallbacks)} state(s) were scanned after the rendered "
                "content and controls became stable while background requests or visual "
                "motion had not settled. Later content updates are outside that snapshot."
            )
            for state in crawler.states:
                fallback = crawler.readiness_fallbacks.get(state.state_id, {})
                if state.status == "scanned" and any(
                    reason in {"pending_requests", "network_idle"}
                    for reason in fallback.get("reasons", [])
                ):
                    runtime_failure = True
                    report.unscanned.append(
                        UnscannedArea(
                            url=state.url,
                            state_id=state.state_id,
                            reason="background_activity_at_scan",
                            detail="Stable rendered content was scanned; outstanding network "
                            "activity may deliver additional content later.",
                            retryable=True,
                        )
                    )
        runtime_failure = runtime_failure or any(
            area.reason.startswith(
                (
                    "navigation_",
                    "http_",
                    "state_budget",
                    "depth_budget",
                    "action_budget",
                    "duration_budget",
                    "authentication_required",
                    "interaction_",
                    "replay_",
                    "state_unstable",
                    "total_action_budget",
                    "select_option_budget",
                    "scan_interrupted",
                    "page_readiness_timeout",
                )
            )
            for area in report.unscanned
        )
        report.findings = list(repository.all())
        report.coverage = Coverage(
            new_tabs_discovered=sum(action.popup_count for action in crawler.actions),
            urls_discovered=max(1, len(crawler.urls_discovered)),
            urls_scanned=len(crawler.urls_scanned),
            states_discovered=len(crawler.states),
            states_scanned=sum(state.status == "scanned" for state in crawler.states),
            interactive_elements_discovered=sum(
                len(s.interactive_elements) for s in crawler.states
            ),
            interactive_elements_tested=len(crawler.tested_elements),
            accessibility_checks_executed=sum(
                rule.outcome != "inapplicable" for result in report.results for rule in result.rules
            ),
        )
        report.summary.severity_counts = {
            impact: sum(
                finding.rule_impact == impact and finding.classification == "VIOLATION"
                for finding in report.findings
            )
            for impact in Impact
        }
        if not crawler.tested_elements and any(
            action.reason == "source_refreshed" for action in crawler.actions
        ):
            runtime_failure = True
            report.unscanned.append(
                UnscannedArea(
                    url=report.application.url,
                    reason="interaction_source_changed",
                    detail=(
                        "Controls changed before they could be used. "
                        "Interaction coverage is incomplete."
                    ),
                    retryable=True,
                )
            )
        report.scan.status = (
            "failed"
            if not report.coverage.states_scanned
            else "partial"
            if runtime_failure
            else "completed"
        )
        report.scan.completed_at = datetime.now(UTC)
        await JSONReportWriter().write(report, settings.output_dir)
        await HTMLReportWriter().write(report, settings.output_dir)
        event("reports_generated")
        return report
