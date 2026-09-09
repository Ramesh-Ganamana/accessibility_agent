import hashlib
import json
from typing import Any

from accessibility_agent.utils.url_utils import normalize_url


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def fingerprint(url: str, snapshot: dict[str, Any]) -> tuple[str, str]:
    dom_hash = digest(
        {
            "structure": snapshot["structure"],
            "text": " ".join(snapshot["text"].split()),
            "dialogs": snapshot["dialogs"],
            "tabs": snapshot["tabs"],
            "ui": snapshot.get("ui", []),
            "form_values": snapshot.get("form_values", []),
        }
    )
    return digest([normalize_url(url), dom_hash]), dom_hash
