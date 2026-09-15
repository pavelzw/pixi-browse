"""Prefetching the list entries next to the highlighted one.

The scheduling helpers are exercised on their own with loads that finish on
command; the app is then run against the offline channel to check that the
entries around the highlight really end up in its caches.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from rattler.networking import Client
from rattler.platform import Platform

from pixi_browse.repodata import query_package_records
from pixi_browse.tui import CondaMetadataTui
from pixi_browse.tui.prefetch import InFlightLoads, Prefetcher, likely_next_indices
from pixi_browse.tui.version_loader import VersionDataLoader
from tests.helpers import (
    TERMINAL_SIZE,
    AppFactory,
    GatewayFactory,
    open_versions,
    wait_for_idle,
)


def test_likely_next_indices_lists_neighbours_then_pages_then_the_tail() -> None:
    indices = likely_next_indices(10, 100, window=2, page=20, tail=2)

    assert indices == [11, 9, 12, 8, 30, 0, 99, 98]


def test_likely_next_indices_stays_inside_the_list_and_skips_repeats() -> None:
    assert likely_next_indices(0, 3, window=5, page=10, tail=2) == [1, 2]
    assert likely_next_indices(2, 3, window=1, page=10, tail=2) == [1, 0]
    assert likely_next_indices(0, 1, window=5, page=10, tail=2) == []
    assert likely_next_indices(0, 0, window=5, page=10, tail=2) == []


class _ControlledLoads:
    """Loads that finish when the test says so, tracking what was asked."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.loaded: set[str] = set()
        self.tasks: list[asyncio.Task[None]] = []
        self.log: list[str] = []
        self._release: dict[str, asyncio.Event] = {}

    async def load(self, key: str) -> None:
        self.started.append(key)
        event = self._release.setdefault(key, asyncio.Event())
        await event.wait()
        self.loaded.add(key)

    def needs_load(self, key: str) -> bool:
        return key not in self.loaded

    def spawn(self, load: Awaitable[None]) -> None:
        self.tasks.append(asyncio.ensure_future(load))

    async def finish(self, key: str) -> None:
        self._release.setdefault(key, asyncio.Event()).set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    def prefetcher(self, max_parallel: int) -> Prefetcher[str]:
        return Prefetcher(
            name="prefetch",
            load=self.load,
            needs_load=self.needs_load,
            spawn=self.spawn,
            log=self.log.append,
            max_parallel=max_parallel,
        )


def test_prefetcher_loads_in_order_a_few_at_a_time() -> None:
    async def run() -> None:
        loads = _ControlledLoads()
        prefetcher = loads.prefetcher(max_parallel=2)

        prefetcher.schedule(["a", "b", "c", "d"])
        await asyncio.sleep(0)
        assert loads.started == ["a", "b"]
        assert prefetcher.running == {"a", "b"}
        assert prefetcher.queued == ["c", "d"]

        await loads.finish("a")
        assert loads.started == ["a", "b", "c"]
        assert prefetcher.running == {"b", "c"}
        assert prefetcher.queued == ["d"]

        for key in ("b", "c", "d"):
            await loads.finish(key)
        assert loads.loaded == {"a", "b", "c", "d"}
        assert prefetcher.running == set()
        assert prefetcher.queued == []
        # The loads start once the event loop runs them, when both slots are
        # already taken.
        assert loads.log[:3] == [
            "prefetch: queued 4 [a, b, c, d] dropped=0 running=0",
            "prefetch: loading a running=2 queued=2",
            "prefetch: loading b running=2 queued=2",
        ]
        assert any(line.startswith("prefetch: loaded a in ") for line in loads.log)

    asyncio.run(run())


def test_prefetcher_replaces_the_queue_but_lets_running_loads_finish() -> None:
    async def run() -> None:
        loads = _ControlledLoads()
        prefetcher = loads.prefetcher(max_parallel=1)

        prefetcher.schedule(["a", "b", "c"])
        await asyncio.sleep(0)
        # The highlight moved on: "b" is no longer wanted, "a" keeps loading.
        prefetcher.schedule(["a", "c", "x"])
        assert prefetcher.running == {"a"}
        assert prefetcher.queued == ["c", "x"]
        assert "prefetch: queued 2 [c, x] dropped=1 running=1" in loads.log

        prefetcher.clear()
        assert prefetcher.queued == []
        assert prefetcher.running == {"a"}

        await loads.finish("a")
        assert loads.loaded == {"a"}
        assert loads.started == ["a"]

    asyncio.run(run())


def test_prefetcher_skips_loaded_keys_and_repeats() -> None:
    async def run() -> None:
        loads = _ControlledLoads()
        loads.loaded.add("done")
        prefetcher = loads.prefetcher(max_parallel=3)

        prefetcher.schedule(["done", "a", "a", "done"])
        await asyncio.sleep(0)
        assert loads.started == ["a"]

        # Scheduling the same keys again neither restarts nor logs anything.
        log_length = len(loads.log)
        prefetcher.schedule(["done", "a"])
        assert loads.started == ["a"]
        assert len(loads.log) == log_length

        await loads.finish("a")

    asyncio.run(run())


def test_prefetcher_logs_and_reraises_a_failed_load() -> None:
    async def run() -> None:
        log: list[str] = []
        tasks: list[asyncio.Task[None]] = []

        async def fail(key: str) -> None:
            raise RuntimeError(f"no {key}")

        prefetcher: Prefetcher[str] = Prefetcher(
            name="prefetch",
            load=fail,
            needs_load=lambda key: True,
            spawn=lambda load: tasks.append(asyncio.ensure_future(load)),
            log=log.append,
        )
        prefetcher.schedule(["a"])
        await asyncio.sleep(0)

        assert len(tasks) == 1
        assert isinstance(tasks[0].exception(), RuntimeError)
        assert prefetcher.running == set()
        assert any(
            line.startswith("prefetch: failed a after ")
            and line.endswith(": RuntimeError('no a')")
            for line in log
        )

    asyncio.run(run())


def test_in_flight_loads_share_one_load_between_waiters() -> None:
    async def run() -> None:
        loads: InFlightLoads[str, int] = InFlightLoads()
        release = asyncio.Event()
        calls = 0

        async def load() -> int:
            nonlocal calls
            calls += 1
            await release.wait()
            return 42

        first = asyncio.ensure_future(loads.run("key", load))
        second = asyncio.ensure_future(loads.run("key", load))
        await asyncio.sleep(0)
        assert "key" in loads
        assert len(loads) == 1

        # A waiter that gives up does not take the load down with it.
        first.cancel()
        await asyncio.sleep(0)
        assert "key" in loads

        release.set()
        assert await second == 42
        assert calls == 1
        assert "key" not in loads
        assert first.cancelled()

    asyncio.run(run())


def test_in_flight_loads_cancel_stops_every_load() -> None:
    async def run() -> None:
        loads: InFlightLoads[str, int] = InFlightLoads()

        async def load() -> int:
            await asyncio.Event().wait()
            return 0

        waiter = asyncio.ensure_future(loads.run("key", load))
        await asyncio.sleep(0)
        loads.cancel()
        await asyncio.wait([waiter], timeout=1)

        assert "key" not in loads
        assert waiter.cancelled()

    asyncio.run(run())


def test_version_loader_shares_a_running_load_per_preview_key(
    make_gateway: GatewayFactory, rattler_client: Client
) -> None:
    async def run() -> None:
        records = await query_package_records(
            gateway=make_gateway(),
            channel_names=["conda-forge"],
            platforms=[Platform("noarch")],
            package_name="six",
        )
        record = records[0]
        loader = VersionDataLoader(client=rattler_client)
        preview_key = (
            "six",
            str(record.version),
            record.build,
            record.build_number,
            record.subdir,
            record.file_name,
        )
        first = asyncio.ensure_future(
            loader.load_version_details("six", record, preview_key=preview_key)
        )
        await asyncio.sleep(0)
        assert loader.is_loading(preview_key)
        assert not loader.has_artifact_data(preview_key)

        second = await loader.load_version_details(
            "six", record, preview_key=preview_key
        )

        assert await first is second
        assert not loader.is_loading(preview_key)
        assert loader.has_artifact_data(preview_key)

    asyncio.run(run())


def test_startup_prefetches_the_repodata_of_the_neighbouring_packages(
    make_app: AppFactory,
) -> None:
    """The fixture channel lists five packages, all within the prefetch window
    of the first one, so every package is queried before it is highlighted."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await wait_for_idle(pilot)
            assert len(app._visible_package_names) == 5
            assert set(app._package_records_cache) == set(app._visible_package_names)

            # Moving on therefore shows the next package straight away.
            await pilot.press("j")
            assert app._previewed_package == app._visible_package_names[1]
            assert app._pending_preview_package == app._previewed_package

    asyncio.run(run())


def _preview_keys(app: CondaMetadataTui) -> set[tuple[str, str, str, int, str, str]]:
    package_name = app._selected_package
    assert package_name is not None
    return {
        app._version_preview_key(package_name, row.entry)
        for row in app._version_rows
        if row.kind == "entry" and row.entry is not None
    }


def test_opening_versions_prefetches_the_neighbouring_builds(
    make_app: AppFactory,
) -> None:
    """``pixi-browse`` has four builds in the fixture channel, all within the
    prefetch window of the first, so each one is loaded once the app is idle
    and moving down the list shows them without a loading placeholder."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_versions(pilot, package_index=1)
            assert app._selected_package == "pixi-browse"
            expected = _preview_keys(app)
            assert len(expected) == 4
            assert expected <= set(app._version_artifact_data_cache)
            assert app._version_prefetcher.queued == []
            assert app._version_prefetcher.running == set()

            for _ in range(3):
                await pilot.press("j")
                entry = app._highlighted_version_entry()
                assert entry is not None
                assert app._previewed_version_key == app._version_preview_key(
                    "pixi-browse", entry
                )
                assert app._main_panel_shows_version_details()

    asyncio.run(run())


def test_leaving_the_version_list_drops_its_prefetch_queue(
    make_app: AppFactory,
) -> None:
    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_versions(pilot, package_index=1)
            await pilot.press("escape")
            await wait_for_idle(pilot)

            assert app._mode == "packages"
            assert app._version_prefetcher.queued == []
            assert app._version_prefetcher.running == set()

    asyncio.run(run())
