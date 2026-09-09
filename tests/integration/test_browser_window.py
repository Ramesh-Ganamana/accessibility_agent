"""Window geometry must survive the same fresh contexts used by crawl replay."""

import asyncio

import pytest

from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.config.settings import load_settings


@pytest.mark.parametrize("headless", [True, False])
def test_browser_window_and_replay_keep_scan_viewport(headless, demo):
    async def check():
        settings = load_settings(environ={"A11Y_URL": demo[0]})
        settings.browser.headless = headless
        session = ChromiumSession()
        await session.open(settings)
        try:
            # Use only the local fixture, never the user's application.
            await session.navigate(demo[0])
            await session.checkpoint()
            for replay in (False, True):
                if replay:
                    await session.restore()
                assert session.page.viewport_size == {"width": 1920, "height": 1080}
                cdp = await session.page.context.new_cdp_session(session.page)
                try:
                    window = await cdp.send("Browser.getWindowForTarget")
                    if not headless:
                        assert window["bounds"]["windowState"] == "maximized"
                finally:
                    await cdp.detach()
        finally:
            await session.close()

    asyncio.run(check())
