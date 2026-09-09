from urllib.parse import urlsplit, urlunsplit


def origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    return (
        parts.scheme.lower(),
        (parts.hostname or "").lower(),
        parts.port or (443 if parts.scheme == "https" else 80),
    )


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Unsupported URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Embedded credentials are forbidden")
    scheme, host, port = origin(url)
    if ":" in host:
        host = f"[{host}]"
    if port != (443 if scheme == "https" else 80):
        host += f":{port}"
    return urlunsplit((scheme, host, parts.path or "/", parts.query, parts.fragment))
