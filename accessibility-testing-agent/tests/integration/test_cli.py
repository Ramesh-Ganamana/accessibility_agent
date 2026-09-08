import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "command,code,expected",
    [
        (["--help"], 0, "validate-config"),
        (["--version"], 0, "0.1.0"),
        (["validate-config", "--url", "https://example.com"], 0, '"valid": true'),
        (["validate-config"], 2, "Invalid configuration"),
    ],
)
def test_cli(command, code, expected):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("A11Y_") and key != "AI_ENABLED"
    }
    result = subprocess.run(
        [sys.executable, "-m", "accessibility_agent", *command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == code
    assert expected in result.stdout


def test_validation_does_not_print_secrets(tmp_path):
    config = tmp_path / "bad.yaml"
    config.write_text("authentication:\n  password: [TOP-SECRET]\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "accessibility_agent",
            "validate-config",
            "--config",
            str(config),
            "--url",
            "https://example.com",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "TOP-SECRET" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "arguments", [[], ["scan"], ["scan", "--auth-origin", "https://login.example.test"]]
)
def test_prompted_scan_runs_real_browser(demo, tmp_path, monkeypatch, capsys, arguments):
    import importlib

    cli = importlib.import_module("accessibility_agent.main")
    answers = iter([demo[0], "fixture@example.test"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(cli, "getpass", lambda prompt: "fixture-only-password")
    monkeypatch.setenv("A11Y_URL", "https://stale.invalid")
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.chdir(tmp_path)
    original = cli.load_settings

    def bounded_settings(*args, **kwargs):
        settings = original(*args, **kwargs)
        assert settings.ai.enabled is False
        settings.crawl.max_states = 1
        return settings

    monkeypatch.setattr(cli, "load_settings", bounded_settings)
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert cli.main(arguments) in {0, 3}
    output = capsys.readouterr().out
    assert "authenticated" in output
    assert "1 states scanned" in output
    assert "fixture-only-password" not in output
    assert len(opened) == 1
    assert len(list(tmp_path.glob("reports/*/results.json"))) == 1


def test_prompt_cancellation(monkeypatch):
    import importlib

    cli = importlib.import_module("accessibility_agent.main")

    def cancelled(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", cancelled)
    assert cli.main([]) == 130
