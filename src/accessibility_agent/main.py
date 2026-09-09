"""CLI for validation and interactive deterministic scans."""

import argparse
import asyncio
import json
import sys
import webbrowser
from collections.abc import Sequence
from datetime import datetime
from getpass import getpass
from pathlib import Path

import yaml
from pydantic import ValidationError

from accessibility_agent import __version__
from accessibility_agent.agent.orchestrator import ScanOrchestrator
from accessibility_agent.config.settings import load_settings
from accessibility_agent.interaction.coordinator import console_input_handler


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Accessibility testing agent — Phase 2")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "validate-config"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, help="YAML configuration file")
        command.add_argument("--url", help="Application URL (or A11Y_URL)")
        command.add_argument(
            "--auth-origin",
            action="append",
            default=[],
            help="Trusted sign-in origin; repeat for additional identity providers",
        )
        command.add_argument("--username", help="Username (prefer A11Y_USERNAME)")
        command.add_argument(
            "--password", help="Password (prefer A11Y_PASSWORD; CLI can be visible)"
        )
        command.add_argument("--output", type=Path, help="Report directory (default: reports)")
        command.add_argument(
            "--crawl-mode",
            choices=["interactive", "links"],
            help="Interactive state exploration (default) or Phase 1 links mode",
        )
        command.add_argument(
            "--test-environment",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Opt in to unfamiliar clickable controls on authorized QA/test sites",
        )
        command.add_argument(
            "--assist-input",
            action="store_true",
            help="Show Chromium and ask for required setup input after login",
        )
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(arguments or ["scan"])
    interactive = (
        args.command == "scan"
        and args.url is None
        and args.config is None
        and args.username is None
        and args.password is None
    )
    overrides = {
        key: value
        for key, value in (
            ("A11Y_URL", args.url),
            ("A11Y_USERNAME", args.username),
            ("A11Y_PASSWORD", args.password),
        )
        if value is not None
    }
    try:
        if interactive:
            print("Accessibility agent - enter your application login URL.")
            args.url = input("URL: ").strip()
            args.username = input("Username (Enter for a public page): ").strip()
            args.password = getpass("Password: ") if args.username else ""
            overrides = {
                "A11Y_URL": args.url,
                "A11Y_USERNAME": args.username,
                "A11Y_PASSWORD": args.password,
                "AI_ENABLED": "false",
            }
        settings = load_settings(
            args.config, overrides=overrides, environ={} if interactive else None
        )
        if args.auth_origin:
            settings.authentication.allowed_origins = [
                *settings.authentication.allowed_origins,
                *args.auth_origin,
            ]
        if interactive:
            settings.browser.headless = False
            settings.output_dir = Path("reports") / datetime.now().strftime("scan-%Y%m%d-%H%M%S-%f")
        if args.output is not None:
            settings.output_dir = args.output
        if args.crawl_mode is not None:
            settings.crawl.mode = args.crawl_mode
        if args.test_environment is not None:
            settings.crawl.test_environment = args.test_environment
        if args.assist_input:
            settings.browser.headless = False
            settings.input_assistance.enabled = True
    except (EOFError, KeyboardInterrupt):
        print("Scan cancelled before starting.")
        return 130
    except ValidationError as error:
        # Never print error strings or input values: credentials may be present.
        issues = sorted({item["type"] for item in error.errors(include_input=False)})
        print("Invalid configuration: " + ", ".join(issues))
        return 2
    except (OSError, ValueError, yaml.YAMLError):
        print("Cannot load configuration. Check YAML structure, file access and values.")
        return 2
    if args.command == "validate-config":
        # Deliberately do not print URLs/selectors either: these may contain tokens.
        print(
            json.dumps(
                {
                    "valid": True,
                    "phase": 2,
                    "crawl_mode": settings.crawl.mode,
                    "test_environment": settings.crawl.test_environment,
                    "browser": settings.browser.browser,
                    "ai_enabled": settings.ai.enabled,
                }
            )
        )
        return 0
    try:
        if settings.crawl.test_environment and settings.crawl.mode == "interactive":
            print(
                "Testing mode: unfamiliar clickable controls may be exercised and replayed. "
                "Use authorized test data; destructive, submission and network guards stay active."
            )
        print("Opening browser, authenticating and crawling. Please wait for the scan summary.")
        input_handler = (
            console_input_handler
            if interactive or args.assist_input or not settings.browser.headless
            else None
        )
        report = asyncio.run(ScanOrchestrator(input_handler=input_handler).run(settings))
    except KeyboardInterrupt:
        print("Scan interrupted.")
        return 130
    except Exception:
        print("Could not complete report generation. Check output access and runtime installation.")
        return 4
    print(
        f"Scan {report.scan.status}: {report.coverage.states_scanned} states scanned, "
        f"{len(report.findings)} finding groups."
    )
    print(f"HTML: {(settings.output_dir / 'accessibility-report.html').resolve()}")
    if report.authentication is not None:
        print(f"Authentication: {report.authentication.status} ({report.authentication.reason})")
    for area in report.unscanned:
        if area.reason.startswith("user_input_"):
            print(area.detail)
    if interactive:
        try:
            webbrowser.open((settings.output_dir / "accessibility-report.html").resolve().as_uri())
        except OSError:
            print("Open the HTML path above to view your report.")
    print(f"JSON: {(settings.output_dir / 'results.json').resolve()}")
    return {"completed": 0, "partial": 3, "failed": 4}[report.scan.status]
