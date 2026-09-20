"""Load the list entries a user is about to highlight before they do.

Every entry of the package list and of the version list is loaded when it is
highlighted: the package list queries the repodata of one package, the
version list fetches the ``info`` payload of one archive. Both take long
enough over the network to flash a loading placeholder while scrolling. The
helpers here load the entries next to the highlighted one ahead of time, and
share a load that is already running instead of starting it a second time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable, Iterable
from functools import partial
from time import perf_counter


def likely_next_indices(
    index: int,
    count: int,
    *,
    window: int,
    page: int,
    head: int,
    tail: int,
) -> list[int]:
    """The indices a user highlighting ``index`` of ``count`` entries is
    likely to move to, nearest first.

    That is the next ``window`` entries in both directions, alternating and
    down before up because lists are read top to bottom, then the entries a
    page down and a page up (``Ctrl+d`` / ``Ctrl+u``) land on, then the last
    ``tail`` entries ``G`` jumps to and the first ``head`` entries ``gg``
    jumps to. ``index`` itself and repeats are left out.
    """
    candidates: list[int] = []
    # j / k, a few steps in either direction
    for distance in range(1, window + 1):
        candidates.append(index + distance)
        candidates.append(index - distance)
    # Ctrl+d
    candidates.append(min(index + page, count - 1))
    # Ctrl+u
    candidates.append(max(index - page, 0))
    # G
    candidates.extend(range(count - 1, count - 1 - tail, -1))
    # gg
    candidates.extend(range(head))

    indices: list[int] = []
    for candidate in candidates:
        if candidate == index or candidate < 0 or candidate >= count:
            continue
        if candidate not in indices:
            indices.append(candidate)
    return indices


# Loads queued for a slot, at most. Holding down an arrow key asks for one
# load per keypress, far more than can ever run at once; by the time a slot
# comes free only the last few of them are still worth having.
MAX_WAITING_LOADS = 8


def _discard_log(message: str) -> None:
    return None


class _Slot:
    """A load's place in the queue in front of the semaphore.

    A slot is made by the load it belongs to, so the task waiting on it is
    whichever one is running right now: the ``owner``. Cancelling a queued load
    means cancelling that task, and a slot whose owner gave up is a slot that
    must not be handed over -- nobody is left to pass it on.
    """

    __slots__ = ("background", "granted", "owner", "ready")

    def __init__(self, *, background: bool) -> None:
        self.background = background
        self.granted = False
        self.owner = asyncio.current_task()
        self.ready = asyncio.Event()


class InFlightLoads[KeyT: Hashable, ValueT]:
    """One running load per key, at most ``max_parallel`` of them at a time.

    A caller that is cancelled only stops waiting: the load itself runs on,
    because the cache it fills is what the next request for the key reads,
    and because cancelling a request rattler coalesced other requests onto
    fails those too. That leaves a load behind on every entry a held-down
    arrow key moves the highlight over, so the number of them running at once
    is capped here instead: without a cap they all fetch at the same time over
    the same connection and every one of them crawls.

    A load that has not started has nothing to lose, so the queue in front of
    the semaphore is where requests are given up on. Slots go to the newest
    load queued, because the entry highlighted a moment ago is the one being
    waited for while the one highlighted ten presses ago is not, and once more
    than ``max_waiting`` loads queue up, the ones queued longest are
    cancelled. A ``background`` load -- one started only in case its entry is
    highlighted next -- is the last of the queue to be given a slot and the
    first to be given up on.

    Every step is logged. What a load does with its slot goes to ``log_detail``,
    since there is a line of it for every keypress; what should be noticed
    without looking for it -- a load given up on, every load dropped -- goes to
    ``log``.
    """

    def __init__(
        self,
        *,
        max_parallel: int,
        max_waiting: int = MAX_WAITING_LOADS,
        name: str = "loads",
        log: Callable[[str], None] = _discard_log,
        log_detail: Callable[[str], None] = _discard_log,
        describe: Callable[[KeyT], str] = str,
    ) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be at least 1")
        if max_waiting < 1:
            raise ValueError("max_waiting must be at least 1")
        self._max_parallel = max_parallel
        self._max_waiting = max_waiting
        self._name = name
        self._log = log
        self._log_detail = log_detail
        self._describe = describe
        self._tasks: dict[KeyT, asyncio.Future[ValueT]] = {}
        # The loads queued for a slot, the one queued longest first.
        self._waiting: dict[KeyT, _Slot] = {}
        self._running = 0

    def __contains__(self, key: KeyT) -> bool:
        return key in self._tasks

    def __len__(self) -> int:
        return len(self._tasks)

    @property
    def running(self) -> int:
        """How many loads hold a slot and are fetching right now."""
        return self._running

    @property
    def waiting(self) -> list[KeyT]:
        """The keys queued for a slot, the one queued longest first."""
        return list(self._waiting)

    async def run(
        self,
        key: KeyT,
        load: Callable[[], Awaitable[ValueT]],
        *,
        background: bool = False,
    ) -> ValueT:
        """Await the load of ``key``, starting ``load()`` unless one is running."""
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(self._gated(key, load, background=background))
            self._tasks[key] = task
            task.add_done_callback(partial(self._forget, key))
        else:
            still_waiting = self._requeue(key, background=background)
            place = "queued" if still_waiting else "running"
            self._log_detail(
                f"{self._name}: {self._describe(key)} joins the load already "
                f"{place}, {self.state}"
            )
        return await asyncio.shield(task)

    @property
    def state(self) -> str:
        """How full the semaphore and the queue in front of it are."""
        return (
            f"running={self._running}/{self._max_parallel} "
            f"waiting={len(self._waiting)}/{self._max_waiting} "
            f"loads={len(self._tasks)}"
        )

    async def _gated(
        self, key: KeyT, load: Callable[[], Awaitable[ValueT]], *, background: bool
    ) -> ValueT:
        await self._acquire(key, background=background)
        try:
            return await load()
        finally:
            # What the load itself found is logged by whoever runs it; what it
            # leaves behind is its slot, taken over here by the next load in
            # the queue if there is one.
            self._release()
            self._log_detail(
                f"{self._name}: {self._describe(key)} released its slot, {self.state}"
            )

    async def _acquire(self, key: KeyT, *, background: bool) -> None:
        """Wait for one of the ``max_parallel`` slots.

        Nothing is awaited before the slot count is read, so a burst of
        requests within one tick queues up rather than all slipping through.
        """
        label = self._describe(key)
        kind = "prefetch" if background else "waited on"
        if self._running < self._max_parallel:
            self._running += 1
            self._log_detail(f"{self._name}: {label} starts ({kind}), {self.state}")
            return

        slot = _Slot(background=background)
        self._waiting[key] = slot
        if self._drop_longest_waiting():
            # The queue was one too long with this load in it, and this load is
            # the one it gave up on -- a prefetch, since a load being waited on
            # is never the first to go. Its place is gone, so waiting for it to
            # come up would be waiting for good; nothing was started, so there
            # is nothing to unwind either. Giving up looks to the caller like
            # any other load dropped from the queue.
            raise asyncio.CancelledError(
                f"{self._name}: gave up on {label} before it started"
            )
        queued = perf_counter()
        self._log_detail(f"{self._name}: {label} queued ({kind}), {self.state}")
        try:
            await slot.ready.wait()
        except asyncio.CancelledError:
            # This load's own place in the queue, and no other: a key given up
            # on is forgotten, so it can be asked for again, and the load that
            # answers then keeps a place of its own.
            if self._waiting.get(key) is slot:
                del self._waiting[key]
            if slot.granted:
                # A slot was handed over just before this load was dropped;
                # pass it on rather than let it go to waste.
                self._release()
            raise
        self._log_detail(
            f"{self._name}: {label} starts ({kind}) after queueing for "
            f"{perf_counter() - queued:.3f}s, {self.state}"
        )

    def _release(self) -> None:
        """Hand the slot on to the load most worth running, or give it up."""
        while (key := self._next_waiting()) is not None:
            slot = self._waiting.pop(key)
            if slot.owner is None or slot.owner.done():
                # The load gave up while its place was coming up. Handing the
                # slot over would lose it: nothing of that load runs any more
                # to give it back. The next load in the queue gets it instead.
                continue
            slot.granted = True
            slot.ready.set()
            return
        self._running -= 1

    def _next_waiting(self) -> KeyT | None:
        """The newest waiting load, the ones being waited on going first."""
        newest_background: KeyT | None = None
        for key, slot in reversed(self._waiting.items()):
            if not slot.background:
                return key
            if newest_background is None:
                newest_background = key
        return newest_background

    def _most_droppable(self) -> KeyT:
        """The load queued longest, the ones nobody waits on going first."""
        for key, slot in self._waiting.items():
            if slot.background:
                return key
        return next(iter(self._waiting))

    def _requeue(self, key: KeyT, *, background: bool) -> bool:
        """Move a waiting load to the end of the queue: it was asked for
        again, so it is the freshest request there is.

        Returns whether the load was still queued rather than already running.
        """
        slot = self._waiting.get(key)
        if slot is None:
            return False
        if not background:
            slot.background = False
        self._waiting[key] = self._waiting.pop(key)
        return True

    def _drop_longest_waiting(self) -> bool:
        """Cancel the loads queued longest, down to ``max_waiting``.

        Returns whether the load calling this was one of them: it is queueing
        right now, so it cannot be stopped by cancelling it -- a task that
        cancels itself carries on to its next ``await`` first, which is one
        await too many when what it awaits is the place it just lost. It is
        told to stop itself instead.
        """
        current = asyncio.current_task()
        dropped_itself = False
        while len(self._waiting) > self._max_waiting:
            key = self._most_droppable()
            owner = self._waiting.pop(key).owner
            # Forgetting the load here, rather than waiting for the cancelled
            # task's callback, lets a request that comes back for this key
            # start a load again instead of joining a doomed one.
            self._tasks.pop(key, None)
            self._log(
                f"{self._name}: gave up on {self._describe(key)} before it "
                f"started, {self.state}"
            )
            if owner is current:
                dropped_itself = True
            elif owner is not None:
                owner.cancel()
        return dropped_itself

    def _forget(self, key: KeyT, task: asyncio.Future[ValueT]) -> None:
        if self._tasks.get(key) is task:
            del self._tasks[key]
        # Every waiter may have been cancelled by the time the load fails;
        # reading the exception keeps asyncio from reporting it as never
        # retrieved.
        if not task.cancelled():
            task.exception()

    def cancel(self) -> None:
        """Cancel every running load; their results would be stale."""
        if self._tasks or self._waiting:
            self._log(f"{self._name}: dropping every load, {self.state}")
        # The queued loads by their slots rather than by the keys they are
        # registered under: a load dropped from the queue is forgotten while it
        # is still unwinding, and one waiting on a place that is being cleared
        # here has to be stopped whether it is still registered or not.
        doomed: list[asyncio.Future[ValueT] | asyncio.Task[object]] = [
            *self._tasks.values(),
            *(slot.owner for slot in self._waiting.values() if slot.owner is not None),
        ]
        self._tasks.clear()
        self._waiting.clear()
        for task in doomed:
            task.cancel()


class Prefetcher[KeyT: Hashable]:
    """Loads the keys near the highlighted entry, a few at a time.

    ``schedule`` replaces the queue with the keys given, in the order they
    should load, and starts loads while fewer than ``max_parallel`` run. Keys
    that ``needs_load`` reports as loaded or loading are skipped. A running
    load is never cancelled: its key was next to the highlight a moment ago,
    so its result is worth keeping in the cache either way.

    Loads run through ``spawn`` so the app can run them as workers. They are
    handed over as a callable rather than as a coroutine, so that a load whose
    worker is cancelled before the worker ever runs is never started at all.

    Every step is written to a log, so a loading placeholder that does show can
    be traced back to why the entry was not ready: the step-by-step of the queue
    goes to ``log_detail``, only a prefetch given up on or failed to ``log``.
    """

    def __init__(
        self,
        *,
        name: str,
        load: Callable[[KeyT], Awaitable[object]],
        needs_load: Callable[[KeyT], bool],
        spawn: Callable[[Callable[[], Awaitable[None]]], None],
        log: Callable[[str], None] = _discard_log,
        log_detail: Callable[[str], None] = _discard_log,
        describe: Callable[[KeyT], str] = str,
        max_parallel: int,
    ) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be at least 1")
        self._name = name
        self._load = load
        self._needs_load = needs_load
        self._spawn = spawn
        self._log = log
        self._log_detail = log_detail
        self._describe = describe
        self._max_parallel = max_parallel
        self._queue: list[KeyT] = []
        self._running: set[KeyT] = set()

    @property
    def queued(self) -> list[KeyT]:
        return list(self._queue)

    @property
    def running(self) -> set[KeyT]:
        return set(self._running)

    def is_running(self, key: KeyT) -> bool:
        return key in self._running

    @property
    def state(self) -> str:
        """How many of the prefetch slots are taken, and how many keys are
        still queued for one."""
        return (
            f"running={len(self._running)}/{self._max_parallel} "
            f"queued={len(self._queue)}"
        )

    def schedule(self, keys: Iterable[KeyT]) -> None:
        """Replace the queue with ``keys`` and start loading."""
        wanted: list[KeyT] = []
        for key in keys:
            if key in wanted or key in self._running or not self._needs_load(key):
                continue
            wanted.append(key)
        dropped = [key for key in self._queue if key not in wanted]
        self._queue = wanted
        if wanted or dropped:
            self._log_detail(
                f"{self._name}: queued {len(wanted)} "
                f"[{', '.join(self._describe(key) for key in wanted)}]"
                f" dropped={len(dropped)} {self.state}"
            )
        self._start_queued()

    def clear(self) -> None:
        """Drop the queue; the loads already running finish on their own."""
        if self._queue:
            self._log_detail(
                f"{self._name}: dropped {len(self._queue)} queued, {self.state}"
            )
        self._queue.clear()

    def cancel(self) -> None:
        """Drop the queue and the slots the running loads hold, for a caller
        that is cancelling the loads themselves.

        A load gives its slot back on the way out, but a load cancelled before
        it ever ran never gets to: nothing of it runs, not even its cleanup.
        Waiting for it to hand the slot over would mean waiting forever, so the
        slots are given up here instead.
        """
        if self._queue or self._running:
            self._log(f"{self._name}: dropping every prefetch, {self.state}")
        self._queue.clear()
        self._running.clear()

    def _start_queued(self) -> None:
        while self._queue and len(self._running) < self._max_parallel:
            key = self._queue.pop(0)
            if key in self._running or not self._needs_load(key):
                continue
            self._running.add(key)
            self._spawn(partial(self._run, key))

    async def _run(self, key: KeyT) -> None:
        label = self._describe(key)
        self._log_detail(f"{self._name}: loading {label} {self.state}")
        started = perf_counter()
        try:
            await self._load(key)
        except asyncio.CancelledError:
            self._log_detail(f"{self._name}: cancelled {label}")
            raise
        except Exception as exc:
            self._log(
                f"{self._name}: failed {label} after "
                f"{perf_counter() - started:.3f}s: {exc!r}"
            )
            raise
        else:
            self._log_detail(
                f"{self._name}: loaded {label} in {perf_counter() - started:.3f}s"
            )
        finally:
            self._running.discard(key)
            self._start_queued()
