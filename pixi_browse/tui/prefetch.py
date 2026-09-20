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
from dataclasses import dataclass, field
from functools import partial


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


# Retain only a few abandoned foreground requests while the user scrolls.
MAX_WAITING_LOADS = 8


def _discard_log(message: str) -> None:
    pass


@dataclass(eq=False)
class _Load[KeyT, ValueT]:
    key: KeyT
    fetch: Callable[[], Awaitable[ValueT]]
    result: asyncio.Future[ValueT] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )
    task: asyncio.Task[ValueT] | None = None
    waiters: int = 0
    speculative: bool = False


class InFlightLoads[KeyT: Hashable, ValueT]:
    """Deduplicate loads and schedule foreground work before neighboring keys.

    Started loads are shielded: cancelling a rattler query can also fail other
    requests coalesced onto it. Queued work can be discarded safely. Prefetch
    reserves one slot for foreground work and follows the supplied key order.
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
        if max_parallel < 1 or max_waiting < 1:
            raise ValueError("load limits must be at least 1")
        self._max_parallel = max_parallel
        self._max_waiting = max_waiting
        self._name = name
        self._log = log
        self._log_detail = log_detail
        self._describe = describe
        self._loads: dict[KeyT, _Load[KeyT, ValueT]] = {}
        self._queue: list[_Load[KeyT, ValueT]] = []
        self._active: set[_Load[KeyT, ValueT]] = set()

    def __contains__(self, key: KeyT) -> bool:
        return key in self._loads

    def __len__(self) -> int:
        return len(self._loads)

    @property
    def running(self) -> int:
        return len(self._active)

    @property
    def waiting(self) -> list[KeyT]:
        return [job.key for job in self._queue]

    @property
    def waited_for(self) -> list[KeyT]:
        return [job.key for job in self._loads.values() if job.waiters]

    def _new(
        self, key: KeyT, fetch: Callable[[], Awaitable[ValueT]]
    ) -> _Load[KeyT, ValueT]:
        job = _Load(key, fetch)
        self._loads[key] = job
        self._queue.append(job)
        return job

    async def run(
        self,
        key: KeyT,
        load: Callable[[], Awaitable[ValueT]],
        *,
        background: bool = False,
    ) -> ValueT:
        job = self._loads.get(key)
        if job is None or (job.task is not None and job.task.cancelled()):
            job = self._new(key, load)
        elif job in self._queue:
            self._queue.remove(job)
            self._queue.append(job)
        if not background:
            job.waiters += 1
        self._pump()
        self._trim()
        try:
            return await asyncio.shield(job.result)
        finally:
            if not background:
                job.waiters -= 1
            self._trim()
            self._pump()

    def schedule(
        self, keys: Iterable[KeyT], load: Callable[[KeyT], Awaitable[ValueT]]
    ) -> None:
        """Replace speculative work; callers omit cached keys."""
        wanted = list(dict.fromkeys(keys)) if self._max_parallel > 1 else []
        for queued in list(self._queue):
            if queued.speculative and not queued.waiters and queued.key not in wanted:
                self._drop(queued)
        for key in wanted:
            job = self._loads.get(key)
            if job is None:
                job = self._new(key, partial(load, key))
                job.speculative = True
            if job.speculative and job in self._queue:
                self._queue.remove(job)
                self._queue.append(job)
        self._pump()

    def clear(self) -> None:
        """Discard speculative work that has not started."""
        for job in list(self._queue):
            if job.speculative and not job.waiters:
                self._drop(job)

    def _pump(self) -> None:
        while self._queue and self.running < self._max_parallel:
            foreground = [job for job in self._queue if job.waiters]
            abandoned = [job for job in self._queue if not job.speculative]
            job = (
                foreground[-1]
                if foreground
                else (abandoned[-1] if abandoned else self._queue[0])
            )
            if (
                not job.waiters
                and job.speculative
                and self.running >= self._max_parallel - 1
            ):
                return
            self._queue.remove(job)
            self._active.add(job)
            job.task = asyncio.create_task(self._fetch(job))
            job.task.add_done_callback(partial(self._finished, job))
            self._log_detail(f"{self._name}: loading {self._describe(job.key)}")

    async def _fetch(self, job: _Load[KeyT, ValueT]) -> ValueT:
        return await job.fetch()

    def _finished(self, job: _Load[KeyT, ValueT], task: asyncio.Task[ValueT]) -> None:
        # Task callbacks also run when cancellation precedes the first step.
        self._active.discard(job)
        if self._loads.get(job.key) is job:
            del self._loads[job.key]
        if task.cancelled():
            job.result.cancel()
        elif (error := task.exception()) is not None:
            self._log(f"{self._name}: failed {self._describe(job.key)}: {error!r}")
            job.result.set_exception(error)
            job.result.exception()  # A speculative load may have no waiter.
        else:
            job.result.set_result(task.result())
        self._pump()

    def _drop(self, job: _Load[KeyT, ValueT]) -> None:
        self._queue.remove(job)
        if self._loads.get(job.key) is job:
            del self._loads[job.key]
        job.result.cancel()

    def _trim(self) -> None:
        abandoned = [job for job in self._queue if not job.speculative]
        for job in abandoned:
            if len(abandoned) <= self._max_waiting:
                break
            if not job.waiters:
                self._drop(job)
                abandoned = [other for other in abandoned if other is not job]

    async def wait(self) -> None:
        """Wait for scheduled work, including failures, without cancelling it."""
        results = [job.result for job in self._loads.values()]
        if results:
            await asyncio.shield(asyncio.gather(*results, return_exceptions=True))

    def cancel(self) -> None:
        """Discard queued work and cancel active loads on cache invalidation."""
        for job in list(self._queue):
            self._drop(job)
        for job in self._active:
            assert job.task is not None
            job.task.cancel()
        self._loads.clear()
