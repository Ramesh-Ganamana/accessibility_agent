"""Minimize persisted page data; no raw credentials, cookies or storage exports."""

import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class Redactor:
    def __init__(self, secrets: list[str]) -> None:
        self.secrets = sorted((s for s in secrets if s), key=len, reverse=True)

    def text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)(bearer\s+)[\w.\-/+=]+", r"\1[REDACTED]", value)
        value = re.sub(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b", "[REDACTED]", value)
        value = re.sub(
            r"(?i)((?:password|token|secret|authorization|session[_-]?id|api[_-]?key)"
            r"\s*[:=]\s*)[^\s<>\"']+",
            r"\1[REDACTED]",
            value,
        )
        return value

    def url(self, value: str) -> str:
        try:
            parts = urlsplit(value)
            host = parts.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            if parts.port:
                host += f":{parts.port}"
            query = urlencode([(self.text(k), "REDACTED") for k, _ in parse_qsl(parts.query)])
            fragment = parts.fragment.split("?", 1)[0]
            if "=" in fragment or fragment.startswith("eyJ"):
                fragment = "REDACTED"
            return self.text(urlunsplit((parts.scheme, host, parts.path, query, fragment)))
        except ValueError:
            return "[INVALID URL]"

    def html(self, value: str) -> str:
        parser = _SafeSnippet(self)
        parser.feed(value)
        return "".join(parser.output)


class _SafeSnippet(HTMLParser):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__(convert_charrefs=True)
        self.redactor = redactor
        self.output: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "textarea"}:
            self.hidden += 1
        if self.hidden:
            return
        safe = []
        for name, value in attrs:
            if name in {"id", "class", "role", "type", "alt", "tabindex", "for"} or name.startswith(
                "aria-"
            ):
                safe.append(f' {name}="{escape(self.redactor.text(value or ""), quote=True)}"')
        self.output.append(f"<{tag}{''.join(safe)}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "textarea"}:
            self.hidden = max(0, self.hidden - 1)
            return
        if not self.hidden:
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.output.append(escape(self.redactor.text(data)))
