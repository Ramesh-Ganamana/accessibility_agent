import asyncio
from urllib.parse import urlencode

import pytest

from accessibility_agent.authentication.authenticator import SemanticAuthenticator
from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.config.settings import Authentication, load_settings


@pytest.mark.parametrize(
    "markup,expected",
    [
        ("<main>Public page</main>", "not_required"),
        ('<input type="password"><input type="password">', "blocked"),
        ('<input autocomplete="one-time-code">', "blocked"),
    ],
)
def test_auth_detection(demo, markup, expected):
    async def run():
        session = ChromiumSession()
        settings = load_settings(environ={"A11Y_URL": demo[0]})
        await session.open(settings)
        try:
            await session.page.goto(demo[0])
            await session.page.set_content(markup)
            result = await SemanticAuthenticator().authenticate(session, Authentication())
            assert result.status == expected
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "markup",
    [
        """<main><h1>Create an account</h1><form><input type="password"
        autocomplete="new-password"><button>Create account</button></form></main>""",
        """<main><h1>Register</h1><form>
        <input type="password" autocomplete="new-password">
        <input type="password" autocomplete="new-password"><button>Register</button>
        </form></main>""",
        """<nav><a href="/articles">Articles</a></nav><aside><h2>Sign in</h2><form>
        <input type="password" autocomplete="current-password">
        <button>Sign in</button></form></aside>""",
    ],
)
def test_public_registration_or_optional_signin_does_not_require_credentials(demo, markup):
    async def run():
        session = ChromiumSession()
        await session.open(load_settings(environ={"A11Y_URL": demo[0]}))
        try:
            await session.page.goto(demo[0])
            await session.page.set_content(markup)
            result = await SemanticAuthenticator().authenticate(session, Authentication())
            assert result.status == "not_required"
        finally:
            await session.close()

    asyncio.run(run())


def test_username_first_and_selector_overrides(demo):
    async def run():
        session = ChromiumSession()
        settings = load_settings(environ={"A11Y_URL": demo[0]})
        await session.open(settings)
        try:
            await session.page.goto(demo[0])
            await session.page.set_content("""<h1>Sign in</h1>
                <input id="identity" autocomplete="username"><button id="next">Next</button>
                <script>document.querySelector('#next').onclick=()=>{
                  document.body.innerHTML='<input id="pwd" type="password">'+
                    '<button id="go">Sign in</button>';
                  document.querySelector('#go').onclick=()=>{document.body.innerHTML='<nav>Welcome</nav>'};
                };</script>""")
            credentials = Authentication(username="fixture-user", password="fixture-password")
            result = await SemanticAuthenticator().authenticate(session, credentials)
            assert result.status == "authenticated"
            await session.page.set_content("""<input id="custom-user">
                <input id="custom-pass" type="password">
                <button id="custom-submit"
                onclick="document.body.innerHTML='<main id=success>Welcome</main>'">Go</button>""")
            credentials = Authentication(
                username="fixture-user",
                password="fixture-password",
                username_selector="#custom-user",
                password_selector="#custom-pass",
                submit_selector="#custom-submit",
                success_selector="#success",
            )
            result = await SemanticAuthenticator().authenticate(session, credentials)
            assert result.status == "authenticated"
        finally:
            await session.close()

    asyncio.run(run())


def test_cross_origin_form_action_is_blocked(demo):
    async def run():
        session = ChromiumSession()
        settings = load_settings(environ={"A11Y_URL": demo[0]})
        await session.open(settings)
        try:
            await session.page.goto(demo[0])
            await session.page.set_content("""<form action="https://outside.test/">
                <input autocomplete="username"><input type="password">
                <button type="submit">Sign in</button></form>""")
            result = await SemanticAuthenticator().authenticate(
                session, Authentication(username="fixture-user", password="fixture-password")
            )
            assert result.reason == "cross_origin_authentication"
            assert await session.page.locator("input[type=password]").input_value() == ""
        finally:
            await session.close()

    asyncio.run(run())


def test_delayed_login_form(demo):
    async def run():
        session = ChromiumSession()
        await session.open(load_settings(environ={"A11Y_URL": demo[0]}))
        try:
            await session.page.goto(demo[0])
            await session.page.set_content("""<main>Loading...</main><script>
                setTimeout(() => { document.body.innerHTML = `
                  <form onsubmit="event.preventDefault();
                    document.body.innerHTML='<nav>Welcome</nav>'">
                  <input name="username" placeholder="Username">
                  <input type="password" placeholder="Password">
                  <button type="submit">Login</button></form>`; }, 500);
                </script>""")
            result = await SemanticAuthenticator().authenticate(
                session, Authentication(username="fixture-user", password="fixture-password")
            )
            assert result.status == "authenticated"
        finally:
            await session.close()

    asyncio.run(run())


def test_trusted_auth_origin_is_only_allowed_during_login(demo):
    async def run():
        from playwright.async_api import Error

        app = demo[0]
        provider = app.replace("127.0.0.1", "localhost").rstrip("/")
        settings = load_settings(environ={"A11Y_URL": app})
        session = ChromiumSession()
        await session.open(settings)
        try:
            with pytest.raises(Error):
                await session.navigate(provider + "/public")
        finally:
            await session.close()
        settings.authentication.allowed_origins = [provider]
        session = ChromiumSession()
        await session.open(settings)
        try:
            await session.navigate(provider + "/public")
            await session.page.set_content(
                """<form onsubmit="event.preventDefault();
              location.href="""
                + "'"
                + app
                + "public'"
                + """">
              <input autocomplete="username"><input type="password">
              <button type="submit">Sign in</button></form>"""
            )
            credentials = Authentication(
                username="fixture-user",
                password="fixture-password",
                allowed_origins=[provider, app.rstrip("/")],
                success_selector="main",
            )
            result = await SemanticAuthenticator().authenticate(session, credentials)
            assert result.status == "authenticated"
            assert session.page.url.startswith(app)
            session.enable_crawl_policy()
            with pytest.raises(Error):
                await session.navigate(provider + "/public")
        finally:
            await session.close()

    asyncio.run(run())


def test_oauth_signin_discovery_and_username_first_without_heading(demo):
    async def run():
        from playwright.async_api import Error

        app = demo[0]
        provider = "https://identity.example.test"
        url = (
            provider
            + "/oauth2/authorize?"
            + urlencode(
                {"client_id": "fixture", "response_type": "code", "redirect_uri": app + "public"}
            )
        )
        session = ChromiumSession()
        await session.open(load_settings(environ={"A11Y_URL": app}))
        try:
            await session.page.route(
                provider + "/**",
                lambda route: route.fulfill(
                    content_type="text/html",
                    body="""<title>User details</title><input placeholder="Username or email">
                    <button id="continue">Continue</button><script>
                    document.querySelector('#continue').onclick=()=>{
                        document.body.innerHTML='<input type="password">'+
                            '<button id="signin">Sign in</button>';
                        document.querySelector('#signin').onclick=()=>{
                            document.body.innerHTML='<main>Welcome</main>';
                        };
                    };</script>""",
                ),
            )
            await session.navigate(url)
            assert session.authentication_origins == [provider]
            assert session.blocked_requests == 0
            result = await SemanticAuthenticator().authenticate(
                session,
                Authentication(
                    username="fixture-user", password="fixture-password", success_selector="main"
                ),
            )
            assert result.status == "authenticated"
            session.enable_crawl_policy()
            with pytest.raises(Error):
                await session.navigate(url)
        finally:
            await session.close()

    asyncio.run(run())


def test_oauth_redirect_with_unrelated_callback_is_blocked(demo):
    async def run():
        from playwright.async_api import Error

        provider = "https://identity.example.test"
        url = (
            provider
            + "/authorize?"
            + urlencode(
                {
                    "client_id": "fixture",
                    "response_type": "code",
                    "redirect_uri": "https://other.test/",
                }
            )
        )
        session = ChromiumSession()
        await session.open(load_settings(environ={"A11Y_URL": demo[0]}))
        try:
            with pytest.raises(Error):
                await session.navigate(url)
            assert session.authentication_origins == []
            assert session.blocked_auth_origin == provider
        finally:
            await session.close()

    asyncio.run(run())
