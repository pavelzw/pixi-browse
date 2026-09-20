"""Prefetching the list entries next to the highlighted one.

The scheduling helpers are exercised on their own with loads that finish on
command; the app is then run against the offline channel to check that the
entries around the highlight really end up in its caches.
"""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from functools import partial

from rattler.networking import Client
from rattler.platform import Platform

from pixi_browse.repodata import query_package_records
from pixi_browse.tui import CondaMetadataTui
from pixi_browse.tui.prefetch import (
    MAX_WAITING_LOADS,
    InFlightLoads,
    Prefetcher,
    likely_next_indices,
)
from pixi_browse.tui.version_loader import VersionDataLoader
from tests.helpers import (
    TERMINAL_SIZE,
    AppFactory,
    GatewayFactory,
    open_versions,
    wait_for_idle,
)


def test_likely_next_indices_lists_neighbours_pages_then_both_ends() -> None:
    indices = likely_next_indices(10, 100, window=2, page=20, head=2, tail=2)

    # j/k window, Ctrl+d, Ctrl+u, G, then gg (0 already listed by Ctrl+u).
    assert indices == [11, 9, 12, 8, 30, 0, 99, 98, 1]


def test_likely_next_indices_stays_inside_the_list_and_skips_repeats() -> None:
    assert likely_next_indices(0, 3, window=5, page=10, head=2, tail=2) == [1, 2]
    assert likely_next_indices(2, 3, window=1, page=10, head=2, tail=2) == [1, 0]
    assert likely_next_indices(50, 100, window=0, page=0, head=1, tail=1) == [99, 0]
    assert likely_next_indices(0, 1, window=5, page=10, head=2, tail=2) == []
    assert likely_next_indices(0, 0, window=5, page=10, head=2, tail=2) == []


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

    def spawn(self, load: Callable[[], Awaitable[None]]) -> None:
        self.tasks.append(asyncio.ensure_future(load()))

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
            # Both levels go to one list, so a test can assert on every line
            # without caring which of them the app would show.
            log=self.log.append,
            log_detail=self.log.append,
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
            "prefetch: queued 4 [a, b, c, d] dropped=0 running=0/2 queued=4",
            "prefetch: loading a running=2/2 queued=2",
            "prefetch: loading b running=2/2 queued=2",
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
        assert "prefetch: queued 2 [c, x] dropped=1 running=1/1 queued=2" in loads.log

        prefetcher.clear()
        assert prefetcher.queued == []
        assert prefetcher.running == {"a"}

        await loads.finish("a")
        assert loads.loaded == {"a"}
        assert loads.started == ["a"]

    asyncio.run(run())


def test_prefetcher_cancel_frees_the_slots_of_loads_that_never_ran() -> None:
    """Cancelling the workers is what the app does when the selection they
    belong to is replaced, and a worker cancelled before its first step runs
    nothing at all -- not the load, not the cleanup that frees its slot."""

    async def run() -> None:
        loads = _ControlledLoads()
        prefetcher = loads.prefetcher(max_parallel=2)

        prefetcher.schedule(["a", "b", "c"])
        prefetcher.cancel()
        for task in loads.tasks:
            task.cancel()
        await asyncio.sleep(0)
        assert loads.started == []
        assert prefetcher.running == set()
        assert prefetcher.queued == []
        assert "prefetch: dropping every prefetch, running=2/2 queued=1" in loads.log

        # Both slots are free again, so the next selection is prefetched.
        prefetcher.schedule(["d", "e"])
        await asyncio.sleep(0)
        assert loads.started == ["d", "e"]

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
            spawn=lambda load: tasks.append(asyncio.ensure_future(load())),
            log=log.append,
            log_detail=log.append,
            max_parallel=1,
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
        loads: InFlightLoads[str, int] = InFlightLoads(max_parallel=1)
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


class _GatedLoads:
    """Keys loaded through an ``InFlightLoads``, on the test's command."""

    def __init__(
        self, *, max_parallel: int, max_waiting: int = MAX_WAITING_LOADS
    ) -> None:
        self.started: list[str] = []
        self.log: list[str] = []
        self.requests: dict[str, asyncio.Task[str]] = {}
        self._release: dict[str, asyncio.Event] = {}
        self.loads: InFlightLoads[str, str] = InFlightLoads(
            max_parallel=max_parallel,
            max_waiting=max_waiting,
            name="loads",
            log=self.log.append,
            log_detail=self.log.append,
        )

    async def _load(self, key: str) -> str:
        self.started.append(key)
        await self._release.setdefault(key, asyncio.Event()).wait()
        return key

    def request(self, key: str, *, background: bool = False) -> asyncio.Task[str]:
        """Ask for ``key`` the way a highlight or a prefetch does."""
        request = asyncio.ensure_future(
            self.loads.run(key, partial(self._load, key), background=background)
        )
        self.requests[key] = request
        return request

    async def settle(self, *keys: str, background: bool = False) -> None:
        for key in keys:
            self.request(key, background=background)
        await self._idle()

    def release(self, key: str) -> None:
        """Let the load of ``key`` finish, without waiting for it to."""
        self._release.setdefault(key, asyncio.Event()).set()

    def abandon(self, key: str) -> None:
        """Stop waiting for ``key``, the way a highlight moving on does.

        The load itself runs on: what it fetches is worth caching either way.
        """
        self.requests[key].cancel()

    async def finish(self, key: str) -> None:
        self.release(key)
        await self._idle()

    async def dropped(self, key: str) -> bool:
        """Whether the request for ``key`` was given up on.

        Awaiting the request itself, rather than looking at it after a fixed
        number of ticks, because a load that is dropped travels back through
        a chain of callbacks before the caller it leaves empty-handed sees it.
        """
        try:
            await self.requests[key]
        except asyncio.CancelledError:
            return True
        return False

    @staticmethod
    async def _idle() -> None:
        # One tick starts the request, one lets its load take or wait for a
        # slot.
        await asyncio.sleep(0)
        await asyncio.sleep(0)


def test_in_flight_loads_run_at_most_max_parallel_loads() -> None:
    async def run() -> None:
        gated = _GatedLoads(max_parallel=2)

        await gated.settle("a", "b", "c", "d")
        assert gated.started == ["a", "b"]
        assert gated.loads.running == 2
        assert gated.loads.waiting == ["c", "d"]
        assert gated.log[:4] == [
            "loads: a starts (waited on), running=1/2 waiting=0/8 loads=4",
            "loads: b starts (waited on), running=2/2 waiting=0/8 loads=4",
            "loads: c queued (waited on), running=2/2 waiting=1/8 loads=4",
            "loads: d queued (waited on), running=2/2 waiting=2/8 loads=4",
        ]

        # The freed slot goes to the newest request: the entry highlighted
        # last is the one being waited for.
        await gated.finish("a")
        assert gated.started == ["a", "b", "d"]
        assert gated.loads.waiting == ["c"]

        for key in ("b", "c", "d"):
            await gated.finish(key)
        assert gated.loads.running == 0
        assert gated.loads.waiting == []
        assert await gated.requests["c"] == "c"

    asyncio.run(run())


def test_in_flight_loads_run_a_background_load_last() -> None:
    async def run() -> None:
        gated = _GatedLoads(max_parallel=1)

        await gated.settle("running")
        await gated.settle("prefetched", background=True)
        await gated.settle("highlighted")
        assert gated.started == ["running"]

        await gated.finish("running")
        assert gated.started == ["running", "highlighted"]

        await gated.finish("highlighted")
        assert gated.started == ["running", "highlighted", "prefetched"]

        await gated.finish("prefetched")

    asyncio.run(run())


def test_in_flight_loads_stop_holding_back_a_background_load_asked_for() -> None:
    async def run() -> None:
        gated = _GatedLoads(max_parallel=1)

        await gated.settle("running")
        await gated.settle("prefetched", background=True)
        await gated.settle("highlighted")
        # The prefetched entry has become the highlighted one, so its waiting
        # load is now being waited on and stops going last.
        await gated.settle("prefetched")

        await gated.finish("running")
        assert gated.started == ["running", "prefetched"]

        for key in ("prefetched", "highlighted"):
            await gated.finish(key)
        assert gated.loads.waiting == []

    asyncio.run(run())


def test_in_flight_loads_give_up_on_the_loads_waiting_longest() -> None:
    async def run() -> None:
        gated = _GatedLoads(max_parallel=1, max_waiting=2)

        await gated.settle("running", "stale", "b", "c")
        # Nothing is given up on while every queued load is being waited for.
        assert gated.loads.waiting == ["stale", "b", "c"]

        # The highlight moves on from all three: their loads run on for the
        # cache, but from here they are prefetches in all but name.
        for key in ("stale", "b", "c"):
            gated.abandon(key)
        await gated._idle()
        await gated.settle("highlighted")

        # Only the two newest of the loads nobody waits for keep their place.
        assert gated.loads.waiting == ["c", "highlighted"]
        assert "stale" not in gated.loads
        assert "b" not in gated.loads
        assert any("gave up on stale" in line for line in gated.log)
        assert any("gave up on b" in line for line in gated.log)

        # Asking for a key that was given up on starts a load for it once more.
        await gated.settle("stale")
        assert gated.loads.waiting == ["highlighted", "stale"]
        assert "c" not in gated.loads

        for key in ("running", "stale", "highlighted"):
            await gated.finish(key)
        assert gated.started == ["running", "stale", "highlighted"]

    asyncio.run(run())


def test_in_flight_loads_give_up_on_a_background_load_first() -> None:
    async def run() -> None:
        gated = _GatedLoads(max_parallel=1, max_waiting=2)

        await gated.settle("running")
        await gated.settle("highlighted")
        await gated.settle("prefetched", background=True)
        await gated.settle("newer")

        assert gated.loads.waiting == ["highlighted", "newer"]
        assert await gated.dropped("prefetched")

        # Waiting for a load keeps it from being dropped, however long it has
        # been queued; a load whose caller has gone is the first to go, however
        # recently it was asked for.
        gated.abandon("newer")
        await gated._idle()
        await gated.settle("newest")
        assert gated.loads.waiting == ["highlighted", "newest"]
        assert "newer" not in gated.loads
        assert any("gave up on newer" in line for line in gated.log)

        for key in ("running", "newest", "highlighted"):
            await gated.finish(key)
        assert gated.started == ["running", "newest", "highlighted"]

    asyncio.run(run())


def test_in_flight_loads_never_give_up_on_a_load_being_waited_for() -> None:
    """The load of the highlighted entry queues with all the rest, so a queue
    that is over long may be nothing but loads somebody is waiting for. Giving
    one of those up leaves a loading placeholder on screen with nothing left to
    replace it, so the queue is let over its limit instead -- it is bounded by
    the callers waiting, and they come and go with the highlight."""

    async def run() -> None:
        gated = _GatedLoads(max_parallel=1, max_waiting=2)

        await gated.settle("running")
        await gated.settle("highlighted", "b", "c", "d")

        assert gated.loads.waiting == ["highlighted", "b", "c", "d"]
        assert not any("gave up on" in line for line in gated.log)
        assert any(
            "4 loads queued and every one of them is being waited for" in line
            for line in gated.log
        )

        # Every one of them is waited for, so every one of them gets its turn.
        for key in ("running", "d", "c", "b", "highlighted"):
            await gated.finish(key)
        assert gated.started == ["running", "d", "c", "b", "highlighted"]
        assert await gated.requests["highlighted"] == "highlighted"
        assert gated.loads.running == 0
        assert len(gated.loads) == 0

    asyncio.run(run())


def test_in_flight_loads_start_a_fresh_load_rather_than_join_a_cancelled_one() -> None:
    """A load stays in the table until its own done callback runs, one turn of
    the loop after it ends. A request that arrives in between must not join a
    load that was cancelled in that turn: it would be handed the cancellation
    of a load it had nothing to do with, and the entry it belongs to would wait
    for data that is never coming."""

    async def run() -> None:
        loads: InFlightLoads[str, str] = InFlightLoads(max_parallel=1)
        woken = asyncio.Event()

        async def cancelled_load() -> str:
            # Wakes the request below while this load is still the one in the
            # table, so that it runs before the callback that forgets it.
            woken.set()
            raise asyncio.CancelledError("gave up on it")

        async def load() -> str:
            return "loaded"

        async def request_when_woken() -> str:
            await woken.wait()
            return await loads.run("key", load)

        joining = asyncio.ensure_future(request_when_woken())
        cancelled = asyncio.ensure_future(loads.run("key", cancelled_load))
        await asyncio.wait([joining, cancelled], timeout=1)

        assert cancelled.cancelled()
        assert joining.result() == "loaded"
        assert len(loads) == 0

    asyncio.run(run())


def test_in_flight_loads_give_up_on_a_prefetch_the_queue_has_no_room_for() -> None:
    """A prefetch that arrives at a queue full of loads being waited on is the
    one given up on, and it is given up on before it ever waits: its place in
    the queue is gone, so waiting for that place to come up would be waiting
    for good, holding on to the prefetch slot it was started in."""

    async def run() -> None:
        gated = _GatedLoads(max_parallel=1, max_waiting=2)

        await gated.settle("running")
        await gated.settle("highlighted", "newer")
        await gated.settle("prefetched", background=True)

        assert gated.loads.waiting == ["highlighted", "newer"]
        assert "prefetched" not in gated.loads
        assert await gated.dropped("prefetched")
        assert any("gave up on prefetched" in line for line in gated.log)
        assert not any("prefetched queued" in line for line in gated.log)

        for key in ("running", "newer", "highlighted"):
            await gated.finish(key)
        assert gated.started == ["running", "newer", "highlighted"]
        assert gated.loads.running == 0
        assert len(gated.loads) == 0

    asyncio.run(run())


def test_a_prefetch_the_queue_has_no_room_for_gives_its_slot_back() -> None:
    """The prefetcher only has a few slots, and it is the loads themselves that
    hand them back. One that hangs in the queue in front of the semaphore never
    does, and a prefetcher missing a slot for the rest of the session runs
    fewer and fewer loads until it runs none at all."""

    async def run() -> None:
        gated = _GatedLoads(max_parallel=1, max_waiting=2)
        workers: list[asyncio.Task[None]] = []
        prefetcher: Prefetcher[str] = Prefetcher(
            name="prefetch",
            load=lambda key: gated.loads.run(
                key, partial(gated._load, key), background=True
            ),
            needs_load=lambda key: key not in gated.started,
            spawn=lambda load: workers.append(asyncio.ensure_future(load())),
            log=gated.log.append,
            log_detail=gated.log.append,
            max_parallel=1,
        )

        # A full queue of loads being waited on, then a prefetch behind them.
        await gated.settle("running")
        await gated.settle("highlighted", "newer")
        prefetcher.schedule(["prefetched"])
        # Giving up on the load travels back to the prefetcher through the
        # shielded load and the worker awaiting it; the slot is handed back on
        # the worker's way out, so the worker finishing is what to wait for.
        await asyncio.wait(workers, timeout=1)

        assert prefetcher.running == set()
        assert any("gave up on prefetched" in line for line in gated.log)

        # The slot is free, so the next prefetch runs in it.
        for key in ("running", "newer", "highlighted"):
            await gated.finish(key)
        prefetcher.schedule(["prefetched"])
        await gated._idle()
        assert gated.started[-1] == "prefetched"

    asyncio.run(run())


def test_in_flight_loads_settle_after_a_burst_of_requests() -> None:
    """Whatever a held-down arrow key throws at the gate -- requests joining
    loads, prefetches, callers giving up, keys dropped from a full queue, a
    selection replaced mid-load -- every load that is asked for finishes and
    nothing is left holding a slot or a place in the queue afterwards.
    """

    async def run() -> None:
        rng = random.Random(20260918)
        keys = [f"k{index}" for index in range(8)]
        for round_index in range(200):
            gated = _GatedLoads(max_parallel=4, max_waiting=3)
            requests: list[asyncio.Task[str]] = []
            # The requests that must still come back with a value: waited on,
            # never given up on by the test, and made since the last time the
            # selection they belong to was replaced.
            awaited: list[asyncio.Task[str]] = []

            for _ in range(60):
                action = rng.random()
                key = rng.choice(keys)
                if action < 0.55:
                    background = rng.random() < 0.4
                    request = gated.request(key, background=background)
                    requests.append(request)
                    if not background:
                        awaited.append(request)
                elif action < 0.7:
                    gated.release(key)
                elif action < 0.8 and requests:
                    # A worker whose entry is no longer highlighted.
                    victim = rng.choice(requests)
                    victim.cancel()
                    if victim in awaited:
                        awaited.remove(victim)
                elif action < 0.85:
                    # The selection the loads belong to was replaced.
                    gated.loads.cancel()
                    awaited.clear()
                if rng.random() < 0.5:
                    await asyncio.sleep(0)
                assert gated.loads.running <= 4, (round_index, gated.loads.running)
                queued = gated.loads.waiting
                # The queue only goes over its limit to hold on to loads that
                # are being waited for.
                if len(queued) > 3:
                    waited_for = set(gated.loads.waited_for)
                    assert all(key in waited_for for key in queued), (
                        round_index,
                        queued,
                    )

            for key in keys:
                gated.release(key)
            await asyncio.wait(requests, timeout=5)
            await gated.settle()
            assert gated.loads.running == 0, (round_index, gated.loads.running)
            assert gated.loads.waiting == [], (round_index, gated.loads.waiting)
            assert len(gated.loads) == 0, (round_index, len(gated.loads))
            # Nobody who kept waiting was left empty-handed.
            for request in awaited:
                assert not request.cancelled(), (round_index, request)

    asyncio.run(run())


def test_in_flight_loads_cancel_stops_every_load() -> None:
    async def run() -> None:
        loads: InFlightLoads[str, int] = InFlightLoads(max_parallel=1)

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


def test_in_flight_loads_keep_the_per_keypress_lines_out_of_the_main_log() -> None:
    """A keypress moves the highlight over an entry and logs the slot the load
    takes and gives back. That is a line or three per press, so it belongs in
    the detail log; what nobody would go looking for -- a load given up on --
    belongs in the main one."""

    async def run() -> None:
        main: list[str] = []
        detail: list[str] = []
        loads: InFlightLoads[str, str] = InFlightLoads(
            max_parallel=1,
            max_waiting=1,
            name="loads",
            log=main.append,
            log_detail=detail.append,
        )
        release = asyncio.Event()

        async def load(key: str) -> str:
            await release.wait()
            return key

        # "b" is the prefetch of a neighbouring entry, so it is the one the
        # queue has no room for once "c" is highlighted.
        requests = [
            asyncio.ensure_future(
                loads.run(key, partial(load, key), background=key == "b")
            )
            for key in ("a", "b", "c")
        ]
        # One tick for the requests, one for the loads they start.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait(requests, timeout=5)

        assert main == [
            "loads: gave up on b before it started, running=1/1 waiting=1/1 loads=2"
        ]
        # Only what happened, without the state each line reports and without
        # the seconds a load spent queueing.
        happened = [
            re.sub(r"[0-9]+\.[0-9]+s", "Xs", line.partition(",")[0]) for line in detail
        ]
        assert happened == [
            "loads: a starts (waited on)",
            "loads: b queued (prefetch)",
            "loads: c queued (waited on)",
            "loads: a released its slot",
            "loads: c starts (waited on) after queueing for Xs",
            "loads: c released its slot",
        ]

    asyncio.run(run())


class _Scrolling:
    """A package list of a thousand entries, wired the way the app wires one.

    The fixture channel is five packages long, so paging through a list is
    played out here instead: one waited-on load for the entry highlighted --
    cancelled when the highlight moves on, like the exclusive preview worker --
    a prefetcher with a slot fewer for the entries around it, and the one
    ``InFlightLoads`` both of them load through.
    """

    # As in the app: `_PREFETCH_WINDOW`, `_MAX_PARALLEL_LOADS` and the slot the
    # prefetch leaves to the highlighted entry.
    WINDOW = 5
    PAGE = 20
    MAX_PARALLEL = 4

    def __init__(self) -> None:
        self.keys = [f"p{index:04d}" for index in range(1000)]
        self.cache: dict[str, str] = {}
        self.log: list[str] = []
        self.workers: list[asyncio.Task[None]] = []
        self.preview: asyncio.Task[str] | None = None
        self.loads: InFlightLoads[str, str] = InFlightLoads(
            max_parallel=self.MAX_PARALLEL,
            name="repodata",
            log=self.log.append,
            log_detail=self.log.append,
        )
        self.prefetcher: Prefetcher[str] = Prefetcher(
            name="prefetch",
            load=self._prefetch,
            needs_load=self._needs_load,
            spawn=self._spawn,
            log=self.log.append,
            log_detail=self.log.append,
            max_parallel=self.MAX_PARALLEL - 1,
        )

    async def _fetch(self, key: str) -> str:
        # A query takes a few turns of the event loop, like a few round trips.
        for _ in range(5):
            await asyncio.sleep(0)
        self.cache[key] = key
        return key

    def _needs_load(self, key: str) -> bool:
        return key not in self.cache and key not in self.loads

    async def _load(self, key: str, *, background: bool) -> str:
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        return await self.loads.run(
            key, partial(self._fetch, key), background=background
        )

    async def _prefetch(self, key: str) -> None:
        await self._load(key, background=True)

    def _spawn(self, load: Callable[[], Awaitable[None]]) -> None:
        self.workers.append(asyncio.ensure_future(load()))

    def highlight(self, index: int) -> None:
        """Show the entry at ``index``, loading it unless it is cached, and
        queue the entries the highlight is likely to move to next."""
        if self.preview is not None:
            self.preview.cancel()
            self.preview = None
        key = self.keys[index]
        if key not in self.cache:
            self.preview = asyncio.ensure_future(self._load(key, background=False))
        self.prefetcher.schedule(
            self.keys[candidate]
            for candidate in likely_next_indices(
                index,
                len(self.keys),
                window=self.WINDOW,
                page=self.PAGE,
                head=2,
                tail=2,
            )
        )

    async def settle(self, ticks: int = 400) -> None:
        for _ in range(ticks):
            await asyncio.sleep(0)


def test_paging_down_the_list_settles_with_the_neighbours_prefetched() -> None:
    """Ctrl+d a few times, then wait: by the time the loads are done every entry
    a `j` lands on is in the cache, and nothing holds a slot or a queue place
    that would keep the entries after it from being prefetched."""

    async def run() -> None:
        scrolling = _Scrolling()
        scrolling.highlight(0)
        # Held down: each page jump replaces the queue of the one before.
        index = 0
        for _ in range(4):
            index += scrolling.PAGE
            scrolling.highlight(index)
            await scrolling.settle(3)
        await scrolling.settle()

        assert scrolling.prefetcher.running == set()
        assert scrolling.prefetcher.queued == []
        assert scrolling.loads.running == 0
        assert scrolling.loads.waiting == []
        assert len(scrolling.loads) == 0

        # Down one at a time from there, every entry ready before it is asked for.
        for step in range(1, scrolling.WINDOW + 1):
            key = scrolling.keys[index + step]
            assert key in scrolling.cache, f"{key} was not prefetched"
            scrolling.highlight(index + step)
            await scrolling.settle(30)

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


def test_replacing_the_selection_frees_the_prefetch_slots(
    make_app: AppFactory,
) -> None:
    """Replacing the selection -- a query, a platform or channel change --
    cancels the prefetch workers. One cancelled in the tick it was spawned in
    never runs a line, not even the cleanup that gives its slot back, so the
    prefetcher has to be told or it holds that slot for the rest of the
    session and prefetches nothing ever again."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await wait_for_idle(pilot)
            neighbours = set(app._visible_package_names[1:])
            app._package_records_cache.clear()

            app._prefetch_around_sidebar_highlight(0)
            assert app._package_prefetcher.running
            app._reset_preview_state()
            await wait_for_idle(pilot)

            assert app._package_prefetcher.running == set()
            assert app._package_prefetcher.queued == []

            # Every slot is free again, so the next highlight is prefetched.
            app._prefetch_around_sidebar_highlight(0)
            await wait_for_idle(pilot)
            assert neighbours <= set(app._package_records_cache)

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
