"""Ports for later implementations. Protocols never launch browsers or make requests."""

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Protocol

from playwright.async_api import Page, Response

from accessibility_agent.config.settings import Authentication, Model, Settings
from accessibility_agent.models import (
    AIRecommendation,
    AuthenticationResult,
    Evidence,
    Finding,
    Graph,
    Report,
    ScanResult,
    State,
    UnscannedArea,
)


class BrowserSession(Protocol):
    @property
    def page(self) -> Page: ...
    async def open(self, settings: Settings) -> None: ...
    def enable_crawl_policy(self) -> None: ...
    async def navigate(self, url: str, *, allow_risky: bool = False) -> Response | None: ...
    async def checkpoint(self) -> None: ...
    async def restore(self) -> None: ...
    @property
    def blocked_requests(self) -> int: ...
    @property
    def mutating_requests(self) -> int: ...
    @property
    def authentication_origins(self) -> list[str]: ...
    @property
    def blocked_auth_origin(self) -> str: ...
    async def close(self) -> None: ...


class Authenticator(Protocol):
    async def authenticate(
        self, session: BrowserSession, credentials: Authentication
    ) -> AuthenticationResult: ...


class StateCrawler(Protocol):
    def discover(self, session: BrowserSession) -> AsyncIterator[State]: ...
    def graph(self) -> Graph: ...
    def unscanned(self) -> Sequence[UnscannedArea]: ...


class AccessibilityScanner(Protocol):
    async def scan(self, session: BrowserSession, state: State) -> ScanResult: ...


class EvidenceCollector(Protocol):
    async def capture(
        self, session: BrowserSession, state: State, findings: Sequence[Finding] = ()
    ) -> list[Evidence]: ...


class FindingRepository(Protocol):
    def add(self, findings: Sequence[Finding]) -> None: ...
    def all(self) -> Sequence[Finding]: ...


class SanitizedAIContext(Model):
    """Only privacy-reviewed values may enter this contract; not itself a sanitizer."""

    rule_id: str
    component_role: str
    redacted_context: str
    deterministic_description: str


class AIProvider(Protocol):
    async def analyze(self, context: SanitizedAIContext) -> list[AIRecommendation]: ...


class ReportWriter(Protocol):
    async def write(self, report: Report, output_dir: Path) -> Path: ...


class Orchestrator(Protocol):
    async def run(self, settings: Settings) -> Report: ...
