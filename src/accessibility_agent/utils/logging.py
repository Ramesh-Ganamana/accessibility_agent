"""Structured events with a closed field set, never page errors or credentials."""

import json
import sys
from datetime import UTC, datetime


def event(name: str, *, state_id: str | None = None) -> None:
    print(
        json.dumps({"time": datetime.now(UTC).isoformat(), "event": name, "state_id": state_id}),
        file=sys.stderr,
        flush=True,
    )
