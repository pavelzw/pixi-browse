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
    tail: int,
) -> list[int]:
    """The indices a user highlighting ``index`` of ``count`` entries is
    likely to move to, nearest first.

    That is the next ``window`` entries in both directions, alternating and
    down before up because lists are read top to bottom, then the entries a
    page down and a page up (``Ctrl+d`` / ``Ctrl+u``) land on, and finally
    the last ``tail`` entries ``G`` jumps to. ``index`` itself and repeats are
    left out.
    """
    candidates: list[int] = []
    for distance in range(1, window + 1):
        candidates.append(index + distance)
        candidates.append(index - distance)
    candidates.append(min(index + page, count - 1))
    candidates.append(max(index - page, 0))
    candidates.extend(range(count - 1, count - 1 - tail, -1))

    indices: list[int] = []
    for candidate in candidates:
        if candidate == index or candidate < 0 or candidate >= count:
            continue
        if candidate not in indices:
            indices.append(candidate)
    return indices


class InFlightLoads[KeyT: Hashable, ValueT]:
    """One running load per key, shared by everyone asking for that key.

    A caller that is cancelled only stops waiting: the load itself runs on,
    because the cache it fills is what the next request for the key reads,
    and because cancelling a request rattler coalesced other requests onto
    fails those too.
    """

    def __init__(self) -> None:
        self._tasks: dict[KeyT, asyncio.Future[ValueT]] = {}

    def __contains__(self, key: KeyT) -> bool:
        return key in self._tasks

    def __len__(self) -> int:
        return len(self._tasks)

    async def run(self, key: KeyT, load: Callable[[], Awaitable[ValueT]]) -> ValueT:
        """Await the load of ``key``, starting ``load()`` unless one is running."""
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(load())
            self._tasks[key] = task
            task.add_done_callback(partial(self._forget, key))
        return await asyncio.shield(task)

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
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()


class Prefetcher[KeyT: Hashable]:
    """Loads the keys near the highlighted entry, a few at a time.

    ``schedule`` replaces the queue with the keys given, in the order they
    should load, and starts loads while fewer than ``max_parallel`` run. Keys
    that ``needs_load`` reports as loaded or loading are skipped. A running
    load is never cancelled: its key was next to the highlight a moment ago,
    so its result is worth keeping in the cache either way.

    Loads run through ``spawn`` so the app can run them as workers, and every
    step is written to ``log`` so a loading placeholder that does show can be
    traced back to why the entry was not ready.
    """

    def __init__(
        self,
        *,
        name: str,
        load: Callable[[KeyT], Awaitable[object]],
        needs_load: Callable[[KeyT], bool],
        spawn: Callable[[Awaitable[None]], None],
        log: Callable[[str], None],
        describe: Callable[[KeyT], str] = str,
        max_parallel: int = 3,
    ) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be at least 1")
        self._name = name
        self._load = load
        self._needs_load = needs_load
        self._spawn = spawn
        self._log = log
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
            self._log(
                f"{self._name}: queued {len(wanted)} "
                f"[{', '.join(self._describe(key) for key in wanted)}]"
                f" dropped={len(dropped)} running={len(self._running)}"
            )
        self._start_queued()

    def clear(self) -> None:
        """Drop the queue; the loads already running finish on their own."""
        if self._queue:
            self._log(
                f"{self._name}: dropped {len(self._queue)} queued, "
                f"running={len(self._running)}"
            )
        self._queue.clear()

    def _start_queued(self) -> None:
        while self._queue and len(self._running) < self._max_parallel:
            key = self._queue.pop(0)
            if key in self._running or not self._needs_load(key):
                continue
            self._running.add(key)
            self._spawn(self._run(key))

    async def _run(self, key: KeyT) -> None:
        label = self._describe(key)
        self._log(
            f"{self._name}: loading {label} "
            f"running={len(self._running)} queued={len(self._queue)}"
        )
        started = perf_counter()
        try:
            await self._load(key)
        except asyncio.CancelledError:
            self._log(f"{self._name}: cancelled {label}")
            raise
        except Exception as exc:
            self._log(
                f"{self._name}: failed {label} after "
                f"{perf_counter() - started:.3f}s: {exc!r}"
            )
            raise
        else:
            self._log(
                f"{self._name}: loaded {label} in {perf_counter() - started:.3f}s"
            )
        finally:
            self._running.discard(key)
            self._start_queued()
