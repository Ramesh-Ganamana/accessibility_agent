"""Bounded semantic login discovery; no application-specific selectors."""

import re

from playwright.async_api import Locator, Page, TimeoutError

from accessibility_agent.config.settings import Authentication
from accessibility_agent.interfaces import BrowserSession
from accessibility_agent.models import AuthenticationResult
from accessibility_agent.utils.url_utils import origin


async def visible_unique(locator: Locator) -> Locator | None:
    matches = [item for item in await locator.all() if await item.is_visible()]
    return matches[0] if len(matches) == 1 else None


async def username_field(page: Page, selector: str) -> Locator | None:
    if selector:
        return await visible_unique(page.locator(selector))
    for locator in (
        page.locator('input[autocomplete="username"], input[type="email"]'),
        page.get_by_label(re.compile(r"user|email|e-mail|login", re.I)),
        page.get_by_placeholder(re.compile(r"user|email|e-mail", re.I)),
        page.locator('input[type="text"], input:not([type])'),
    ):
        field = await visible_unique(locator)
        if field is not None and await field.is_editable():
            return field
    return None


async def submit_button(page: Page, field: Locator, selector: str) -> Locator | None:
    if selector:
        return await visible_unique(page.locator(selector))
    form = field.locator("xpath=ancestor::form[1]")
    scope = form if await form.count() else page.locator("body")
    button = await visible_unique(
        scope.get_by_role(
            "button", name=re.compile(r"^(log\s*in|sign\s*in|continue|next|submit)$", re.I)
        )
    )
    if button is not None:
        return button
    # Only infer unnamed submit controls in the field's form.
    if await form.count():
        return await visible_unique(form.locator('button[type="submit"], input[type="submit"]'))
    return None


async def same_origin_submit(
    field: Locator,
    button: Locator,
    start_url: str,
    allowed: list[str] | None = None,
) -> bool:
    trusted = {origin(start_url), *(origin(url) for url in allowed or [])}

    form_action = str(
        await field.evaluate(
            "e => e.form ? e.form.action : location.href"
        )
    )

    button_action = await button.get_attribute("formaction")

    print("========== SUBMIT DEBUG ==========")
    print("START URL:", start_url)
    print("START ORIGIN:", origin(start_url))
    print("FORM ACTION:", form_action)
    print("FORM ACTION ORIGIN:", origin(form_action))
    print("BUTTON FORMACTION:", button_action)
    print("TRUSTED:", trusted)
    print("==================================")

    if button_action:
        button_url = str(await button.evaluate("e => e.formAction"))

        print("BUTTON ACTION URL:", button_url)
        print("BUTTON ACTION ORIGIN:", origin(button_url))

        if origin(button_url) not in trusted:
            print("❌ BLOCKED BY BUTTON ACTION")
            return False

    form_origin = origin(form_action)

    # Some identity-provider forms use javascript:void(0)
    # and perform submission through JavaScript.
    if form_origin[0] in {"http", "https"} and form_origin not in trusted:
        print("❌ BLOCKED BY FORM ACTION")
        return False

    print("✅ SUBMIT ORIGIN TRUSTED")
    return True


class SemanticAuthenticator:
    async def authenticate(
        self, session: BrowserSession, credentials: Authentication
    ) -> AuthenticationResult:
        page = session.page
        start_origin = origin(page.url)
        print("========== AUTH DEBUG ==========")
        print("PAGE URL:", page.url)
        print("PAGE ORIGIN:", origin(page.url))
        print("START ORIGIN:", start_origin)
        print("CREDENTIAL ALLOWED ORIGINS:", credentials.allowed_origins)
        print("SESSION AUTH ORIGINS:", session.authentication_origins)
        print("================================")

        def trusted_origins() -> set[tuple[str, str, int]]:
            # SPA redirects may be discovered while waiting for the login fields.
            return {
                start_origin,
                *(origin(url) for url in credentials.allowed_origins),
                *(origin(url) for url in session.authentication_origins),
            }

        def allowed_origins() -> list[str]:
            return [*credentials.allowed_origins, *session.authentication_origins]

        # SPA login forms can mount after DOMContentLoaded. Wait before discovery.
        if credentials.username.get_secret_value() or credentials.password.get_secret_value():
            try:
                await page.locator(
                    (credentials.username_selector + ":visible")
                    if credentials.username_selector
                    else (
                        'input:not([type="hidden"]):not([type="checkbox"])'
                        ':not([type="radio"]):visible'
                    )
                ).first.wait_for(state="visible")
            except TimeoutError:
                return AuthenticationResult(status="blocked", reason="login_fields_not_identified")
        password = await visible_unique(
            page.locator(credentials.password_selector or 'input[type="password"]')
        )
        if await page.locator(
            'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], input[autocomplete="one-time-code"]'
        ).count():
            return AuthenticationResult(
                status="blocked", reason="captcha_or_mfa_requires_manual_login"
            )
        password_count = await page.locator('input[type="password"]:visible').count()
        if password_count > 1 and not credentials.password_selector:
            return AuthenticationResult(status="blocked", reason="login_password_ambiguous")
        supplied = bool(
            credentials.username.get_secret_value() or credentials.password.get_secret_value()
        )
        if password is None and not supplied:
            return AuthenticationResult(status="not_required")
        if (
            not credentials.username.get_secret_value()
            or not credentials.password.get_secret_value()
        ):
            return AuthenticationResult(status="blocked", reason="credentials_required")
        user = await username_field(page, credentials.username_selector)
        if user is None:
            return AuthenticationResult(status="blocked", reason="login_fields_not_identified")
        print("CURRENT ORIGIN:", origin(page.url))
        print("TRUSTED ORIGINS:", trusted_origins())
        if origin(page.url) not in trusted_origins():
            return AuthenticationResult(status="blocked", reason="cross_origin_authentication")
        await user.fill(credentials.username.get_secret_value())
        if password is None:
            # A username-first flow needs explicit login semantics; never submit an arbitrary form.
            semantic_user = await user.get_attribute("autocomplete") == "username"
            login_heading = await page.get_by_role(
                "heading", name=re.compile(r"log\s*in|sign\s*in", re.I)
            ).count()
            identity_provider = origin(page.url) in {
                origin(url) for url in session.authentication_origins
            }
            if not (
                semantic_user or login_heading or credentials.username_selector or identity_provider
            ):
                return AuthenticationResult(status="blocked", reason="login_fields_not_identified")
            next_button = await submit_button(page, user, credentials.submit_selector)
            if next_button is None:
                return AuthenticationResult(status="blocked", reason="login_submit_ambiguous")
            if not await same_origin_submit(user, next_button, page.url, allowed_origins()):
                return AuthenticationResult(status="blocked", reason="cross_origin_authentication")
            await next_button.click()
            await page.locator(
                credentials.password_selector or 'input[type="password"]'
            ).first.wait_for(state="visible")
            if origin(page.url) not in trusted_origins():
                return AuthenticationResult(status="blocked", reason="cross_origin_authentication")
            password = await visible_unique(
                page.locator(credentials.password_selector or 'input[type="password"]')
            )
        if password is None or await password.get_attribute("autocomplete") == "new-password":
            return AuthenticationResult(status="blocked", reason="login_password_ambiguous")
        button = await submit_button(page, password, credentials.submit_selector)
        if button is None:
            return AuthenticationResult(status="blocked", reason="login_submit_ambiguous")
        # Credential destinations must be explicitly trusted; crawl scope is not login trust.
        if not await same_origin_submit(password, button, page.url, allowed_origins()):
            return AuthenticationResult(status="blocked", reason="cross_origin_authentication")
        await password.fill(credentials.password.get_secret_value())
        await button.click()
        if credentials.success_selector:
            await page.locator(credentials.success_selector).wait_for(state="visible")
        else:
            await password.wait_for(state="hidden")
            await page.locator('nav, [role="navigation"], main a[href]').first.wait_for(
                state="visible"
            )
        if origin(page.url) not in trusted_origins():
            return AuthenticationResult(status="blocked", reason="cross_origin_authentication")
        if await page.locator('input[type="password"]:visible').count():
            return AuthenticationResult(status="failed", reason="login_not_confirmed")
        return AuthenticationResult(status="authenticated")
