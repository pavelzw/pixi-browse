"""The startup repodata load against the offline real-data channel.

These drive the real app like the snapshot tests do, but assert on what
happens rather than on how the screen looks: whether a key press during the
load quits, opens a dialog, or is ignored.
"""

from __future__ import annotations

import asyncio

from rattler.config import Config
from textual.screen import ModalScreen

from pixi_browse.tui.widgets import ChannelScreen, RepodataLoadingScreen
from tests.helpers import (
    MISSING_CHANNEL,
    TERMINAL_SIZE,
    AppFactory,
    wait_for_idle,
    wait_for_screen,
    wait_until,
)


def test_quit_during_startup_load(
    make_app: AppFactory, stalled_linux64_config: Config
) -> None:
    """``q`` quits while the repodata is still loading instead of being queued
    behind the load."""

    async def run() -> None:
        app = make_app(config=stalled_linux64_config)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await wait_for_screen(pilot, RepodataLoadingScreen)
            await pilot.press("q")
            await pilot.pause()
        assert app.return_code == 0

    asyncio.run(run())


def test_dialog_keys_are_ignored_during_startup_load(
    make_app: AppFactory, stalled_linux64_config: Config
) -> None:
    """The platform, channel, compare, MatchSpec, who-needs, search and help
    keys have nothing to act on before the package list exists; the loading
    screen swallows them."""

    async def run() -> None:
        app = make_app(config=stalled_linux64_config)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await wait_for_screen(pilot, RepodataLoadingScreen)
            await pilot.press("p", "c", "C", "m", "w", "slash", "question_mark")
            await pilot.press("escape")
            await pilot.pause()
            assert [type(screen) for screen in app.screen_stack[1:]] == [
                RepodataLoadingScreen
            ]
            app.exit()

    asyncio.run(run())


def test_startup_load_closes_the_loading_screen(make_app: AppFactory) -> None:
    """Once the packages are listed the loading screen is gone and the app is
    back on its main screen."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await wait_for_idle(pilot)
            assert not isinstance(app.screen, ModalScreen)
            app.exit()

    asyncio.run(run())


def test_failed_startup_load_offers_the_channel_selector(
    make_app: AppFactory,
) -> None:
    """After a failed load, ``c`` on the loading screen opens the channel
    selector so the channel can be corrected."""

    async def run() -> None:
        app = make_app(default_channels=(MISSING_CHANNEL,))
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await wait_for_screen(pilot, RepodataLoadingScreen)
            screen = app.screen
            assert isinstance(screen, RepodataLoadingScreen)
            await wait_until(pilot, lambda: screen.failed, what="the load to fail")
            await pilot.press("c")
            await wait_for_screen(pilot, ChannelScreen)
            assert [type(screen) for screen in app.screen_stack[1:]] == [ChannelScreen]
            app.exit()

    asyncio.run(run())
