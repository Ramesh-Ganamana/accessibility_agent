import pytest
from pydantic import ValidationError

from accessibility_agent.config.settings import load_settings


def test_precedence_and_secret_exclusion(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text("application:\n  url: https://file.example\ncrawl:\n  max_states: 7\n")
    settings = load_settings(
        config,
        environ={
            "A11Y_URL": "https://env.example",
            "A11Y_PASSWORD": "private-password",
            "A11Y_USERNAME": "private-user",
        },
        overrides={"A11Y_URL": "https://cli.example"},
    )
    assert settings.application.url == "https://cli.example"
    assert settings.crawl.max_states == 7
    assert settings.authentication.password.get_secret_value() == "private-password"
    for text in (repr(settings), settings.model_dump_json()):
        assert "private-password" not in text
        assert "private-user" not in text
    assert "password" not in settings.model_dump()["authentication"]


@pytest.mark.parametrize(
    "url",
    ["", "file:///tmp/test", "https://u:p@example.com", "https://example.com:bad", "relative/path"],
)
def test_bad_urls(url):
    with pytest.raises(ValidationError):
        load_settings(environ={"A11Y_URL": url})


@pytest.mark.parametrize(
    "body",
    [
        "[]",
        "crawl: {max_states: 0}",
        "unknown: true",
        "browser: {browser: firefox}",
        "crawl: {timeout: -1}",
        "ai: {enabled: nonsense}",
    ],
)
def test_bad_configuration(tmp_path, body):
    config = tmp_path / "bad.yaml"
    config.write_text(body)
    with pytest.raises((ValueError, ValidationError)):
        load_settings(config, environ={"A11Y_URL": "https://example.com"})


def test_defaults_and_false_environment():
    settings = load_settings(environ={"A11Y_URL": "https://example.com", "AI_ENABLED": "false"})
    assert settings.crawl.same_origin_only
    assert not settings.ai.enabled
    assert settings.crawl.allowed_action_ids == []


def test_missing_url():
    with pytest.raises(ValidationError):
        load_settings(environ={})


def test_phase2_defaults_and_form_secret_exclusion():
    settings = load_settings(environ={"A11Y_URL": "https://example.com"})
    assert settings.crawl.mode == "interactive"
    settings.crawl.form_values = {"#query": "private-test-value"}
    assert "private-test-value" not in repr(settings)
    assert "private-test-value" not in settings.model_dump_json()
    assert "form_values" not in settings.model_dump()["crawl"]
    with pytest.raises(ValidationError):
        settings.crawl.max_actions = 0


@pytest.mark.parametrize(
    "value",
    [
        "https://login.example/a",
        "https://login.example?token=x",
        "https://u:p@login.example",
        "file:///login",
    ],
)
def test_auth_allowlist_requires_exact_origins(value):
    from accessibility_agent.config.settings import Authentication

    with pytest.raises(ValidationError):
        Authentication(allowed_origins=[value])
