"""Validated configuration with explicit, predictable source precedence."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, hide_input_in_errors=True)


class Application(Model):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("An absolute HTTP(S) URL is required")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL credentials are forbidden")
        _ = parsed.port
        return value


class Authentication(Model):
    allowed_origins: list[str] = Field(default_factory=list)
    timeout: int = Field(default=60000, ge=100, le=300000)

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            validated = Application.validate_url(value)
            parts = urlsplit(validated)
            if parts.path not in {"", "/"} or parts.query or parts.fragment:
                raise ValueError("Authentication allowlist entries must be origins only")
            result.append(validated.rstrip("/"))
        return result

    username: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    password: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    username_selector: str = ""
    password_selector: str = ""
    submit_selector: str = ""
    success_selector: str = ""


class Viewport(Model):
    width: int = Field(default=1920, ge=320, le=7680)
    height: int = Field(default=1080, ge=240, le=4320)


class Browser(Model):
    browser: Literal["chromium"] = "chromium"
    headless: bool = True
    viewport: Viewport = Field(default_factory=Viewport)


class Crawl(Model):
    mode: Literal["interactive", "links"] = "interactive"
    test_environment: bool = False
    max_depth: int = Field(default=20, ge=0, le=100)
    max_states: int = Field(default=500, ge=1, le=100000)
    timeout: int = Field(default=30000, ge=100, le=300000)
    max_actions_per_state: int = Field(default=100, ge=1)
    max_duration_seconds: int = Field(default=3600, ge=1)
    same_origin_only: bool = True
    allowed_action_ids: list[str] = Field(default_factory=list)
    settle_ms: int = Field(default=200, ge=0, le=10000)
    stability_timeout_ms: int = Field(default=60000, ge=100, le=300000)
    dom_quiet_ms: int = Field(default=1000, ge=100, le=10000)
    network_idle_ms: int = Field(default=500, ge=100, le=10000)
    ready_selector: str = ""
    loading_selectors: list[str] = Field(default_factory=list)
    max_actions: int = Field(default=2000, ge=1, le=100000)
    max_replay_steps: int = Field(default=40, ge=0, le=200)
    max_select_options: int = Field(default=10, ge=1, le=100)
    ignore_selectors: list[str] = Field(default_factory=list)
    form_values: dict[str, SecretStr] = Field(default_factory=dict, exclude=True, repr=False)
    allowed_request_urls: list[str] = Field(default_factory=list, repr=False)

    @field_validator("allowed_request_urls")
    @classmethod
    def validate_request_urls(cls, values: list[str]) -> list[str]:
        return [Application.validate_url(value) for value in values]


class Accessibility(Model):
    standard: Literal["WCAG"] = "WCAG"
    version: Literal["2.1", "2.2"] = "2.2"
    level: Literal["A", "AA", "AAA"] = "AA"


class Feature(Model):
    enabled: bool = True


class AI(Model):
    enabled: bool = False
    provider: str | None = None
    model: str | None = None


class InputAssistance(Model):
    enabled: bool = True
    detection_wait_ms: int = Field(default=1500, ge=0, le=30000)
    timeout_seconds: int = Field(default=600, ge=1, le=3600)
    blocking_selectors: list[str] = Field(default_factory=list)
    max_prompts: int = Field(default=10, ge=1, le=100)


class Settings(Model):
    application: Application
    authentication: Authentication = Field(default_factory=Authentication)
    browser: Browser = Field(default_factory=Browser)
    crawl: Crawl = Field(default_factory=Crawl)
    accessibility: Accessibility = Field(default_factory=Accessibility)
    keyboard: Feature = Field(default_factory=Feature)
    visual: Feature = Field(default_factory=Feature)
    ai: AI = Field(default_factory=AI)
    input_assistance: InputAssistance = Field(default_factory=InputAssistance)
    output_dir: Path = Path("reports")


def load_settings(
    config: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> Settings:
    """Precedence: defaults < YAML < supported environment variables < CLI.

    Do not expose raw validation errors: they can contain rejected secrets.
    No .env discovery or writes occur.
    """
    data: dict[str, Any] = {}
    if config is not None:
        loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
        if loaded is not None and not isinstance(loaded, dict):
            raise ValueError("Configuration must be a YAML mapping")
        data = loaded or {}
    env = os.environ if environ is None else environ
    keys = {
        "A11Y_URL": ("application", "url"),
        "A11Y_USERNAME": ("authentication", "username"),
        "A11Y_PASSWORD": ("authentication", "password"),
        "AI_ENABLED": ("ai", "enabled"),
    }
    for source in (env, overrides or {}):
        for key, (section, field) in keys.items():
            if key in source:
                if section not in data:
                    data[section] = {}
                if not isinstance(data[section], dict):
                    raise ValueError("Configuration section must be a mapping")
                data[section][field] = source[key]
    return Settings.model_validate(data)
