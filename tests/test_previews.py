"""Preview loading behaviour of the real app against the fixture channel."""

from __future__ import annotations

import asyncio

from textual.worker import WorkerState

from tests.helpers import TERMINAL_SIZE, AppFactory, wait_for_idle


def test_reset_preview_state_cancels_in_flight_preview(make_app: AppFactory) -> None:
    """A channel or platform switch resets the selection while a preview may
    still be loading. That worker must be cancelled: it must not render the
    old selection, fill the freshly cleared record cache, or suppress the next
    request for the same package."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await wait_for_idle(pilot)

            app._request_package_preview("pixi-browse")
            request = app._package_preview_request
            assert request is not None
            package_name, worker = request
            assert package_name == "pixi-browse"
            # Let the worker start its gateway query, then reset while that
            # request is in flight, as a platform or channel switch does.
            await asyncio.sleep(0)
            assert worker.is_running

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
