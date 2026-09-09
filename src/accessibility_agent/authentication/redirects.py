"""Recognize application-initiated OAuth/OIDC authorization redirects."""

from urllib.parse import parse_qs, urlsplit

from accessibility_agent.utils.url_utils import normalize_url, origin


def authorization_origin(url: str, application_url: str) -> str | None:
    """Require HTTPS authorization and a callback to the configured application."""
    try:
        normalized = normalize_url(url)
        parts = urlsplit(normalized)
        if parts.scheme != "https" or parts.path.rstrip("/").rsplit("/", 1)[-1] not in {
            "authorize",
            "auth",
        }:
            return None
        query = parse_qs(parts.query)
        if any(
            len(query.get(key, [])) != 1 for key in ("client_id", "redirect_uri", "response_type")
        ):
            return None
        response_types = set(query["response_type"][0].split())
        if not response_types or not response_types <= {"code", "token", "id_token"}:
            return None
        callback = normalize_url(query["redirect_uri"][0])
        if origin(callback) != origin(application_url):
            return None
        return f"{parts.scheme}://{parts.netloc}"
    except ValueError:
        return None
