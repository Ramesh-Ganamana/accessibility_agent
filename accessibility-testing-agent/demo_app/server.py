"""Dependency-free local demo with real cookie-backed login."""

import argparse
import hmac
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

LOGIN = Path(__file__).with_name("login.html").read_text(encoding="utf-8")


def dashboard(extra: bool = False) -> str:
    links = (
        '<a href="/broken">Broken page</a><a href="/redirect">External redirect</a>'
        '<a href="/redirect-danger">Dangerous redirect</a>'
        '<a href="/redirect-hop">Chained redirect</a>'
        if extra
        else ""
    )
    return (
        Path(__file__)
        .with_name("dashboard.html")
        .read_text(encoding="utf-8")
        .replace("{{EXTRA_LINKS}}", links)
    )


ORDERS = Path(__file__).with_name("orders.html").read_text(encoding="utf-8")


def make_server(
    username: str,
    password: str,
    port: int = 0,
    extra: bool = False,
    landing: str = "/dashboard",
    pages: dict[str, str] | None = None,
) -> ThreadingHTTPServer:
    sessions: set[str] = set()
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass  # Never log request payloads or session cookies.

        def respond(self, body: str, status: int = 200) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def redirect(self, location: str, cookie: str | None = None) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            requests.append(path)
            if path in {"/", "/login"}:
                self.respond(LOGIN)
                return
            if path == "/public":
                self.respond(ORDERS.replace("/dashboard", "/public"))
                return
            cookie = self.headers.get("Cookie", "")
            if not any(f"demo_session={session}" in cookie for session in sessions):
                self.redirect("/login")
                return
            if pages and path in pages:
                self.respond(pages[path])
            elif path == "/interactive":
                self.respond(
                    Path(__file__).with_name("interactive.html").read_text(encoding="utf-8")
                )
            elif path == "/dashboard":
                self.respond(dashboard(extra))
            elif path == "/orders":
                self.respond(ORDERS)
            elif path == "/redirect":
                self.redirect("https://example.org/")
            elif path == "/redirect-danger":
                self.redirect("/delete")
            elif path == "/redirect-hop":
                self.redirect("/redirect-danger")
            elif path == "/delete":
                self.respond("Destructive endpoint must never be visited", 409)
            else:
                self.respond("Fixture server error", 500)

        def do_POST(self) -> None:
            requests.append("POST " + urlsplit(self.path).path)
            body = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 10000))
            values = parse_qs(body.decode())
            if (
                self.path == "/login"
                and hmac.compare_digest(values.get("email", [""])[0], username)
                and hmac.compare_digest(values.get("pass", [""])[0], password)
            ):
                token = secrets.token_hex(24)
                sessions.add(token)
                self.redirect(landing, f"demo_session={token}; HttpOnly; SameSite=Strict; Path=/")
            else:
                self.respond(LOGIN + '<p role="alert">Invalid credentials</p>', 401)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.request_paths = requests  # type: ignore[attr-defined]
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local accessibility demo; credentials via env")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interactive", action="store_true", help="Use the Phase 2 UI demo")
    args = parser.parse_args()
    user, password = os.environ.get("A11Y_USERNAME"), os.environ.get("A11Y_PASSWORD")
    if not user or not password:
        parser.error("Set A11Y_USERNAME (email format) and A11Y_PASSWORD first")
    server = make_server(
        user, password, args.port, landing="/interactive" if args.interactive else "/dashboard"
    )
    print(f"Local demo: http://127.0.0.1:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
