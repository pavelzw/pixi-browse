"""Preview loading behaviour of the real app against the fixture channel."""

from __future__ import annotations

import asyncio

from textual.widgets import Static
from textual.worker import WorkerState

from pixi_browse.tui import FileActionScreen
from pixi_browse.tui.app import _PREVIEW_MAX_BYTES
from tests.helpers import TERMINAL_SIZE, AppFactory, open_versions, wait_for_idle


def test_reset_preview_state_cancels_in_flight_preview(make_app: AppFactory) -> None:
    """A channel or platform switch drops the record caches and resets the
    selection while a preview may still be loading. That worker must be
    cancelled: it must not render the old selection, fill the fresh record
    cache, or suppress the next request for the same package."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await wait_for_idle(pilot)

            # Evict a completed prefetch if it won the race with this test.
            # Under CI load it may still be queued, which is fine: the
            # foreground request below joins or starts the same load.
            app._package_records_cache.pop("pixi-browse", None)
            app._request_package_preview("pixi-browse")
            request = app._package_preview_request
            assert request is not None
            package_name, worker = request
            assert package_name == "pixi-browse"
            # Let the worker start its gateway query, then reset while that
            # request is in flight, as a platform or channel switch does.
            await asyncio.sleep(0)
            assert worker.is_running

            app._clear_record_caches()
            app._reset_preview_state()

            assert app._package_preview_request is None
            assert app._version_preview_request is None
            assert worker.is_cancelled
            while not worker.is_finished:
                await asyncio.sleep(0)
            await pilot.pause()
            assert worker.state is WorkerState.CANCELLED
            assert "pixi-browse" not in app._package_records_cache
            assert app._previewed_package is None

            app._request_package_preview("pixi-browse")
            new_request = app._package_preview_request
            assert new_request is not None
            assert new_request[1] is not worker
            await wait_for_idle(pilot)
            assert app._previewed_package == "pixi-browse"
            assert "pixi-browse" in app._package_records_cache

    asyncio.run(run())


def test_file_action_dialog_does_not_read_a_file_too_large_to_preview(
    make_app: AppFactory,
) -> None:
    """Naming the type of a file means reading all of it. A file the preview
    refuses on its size alone must not be read for that: the dialog says so on
    its ``Type:`` line and starts no fetch."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await wait_for_idle(pilot)
            await open_versions(pilot, package_index=1)
            entry = app._highlighted_version_entry()
            assert entry is not None

            app._open_file_action_screen(
                "pixi-browse",
                entry,
                "info/about.json",
                _PREVIEW_MAX_BYTES + 1,
                None,
            )
            await pilot.pause()

            assert app._file_bytes_fetch is None
            screen = app.screen
            assert isinstance(screen, FileActionScreen)
            line = str(screen.query_one("#file-action-type", Static).content)
            assert line == "Type: not detected (256.0 KiB, too large to read)"

    asyncio.run(run())
