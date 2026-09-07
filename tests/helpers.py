"""Helpers shared by the test-suite: local channel server and pilot utilities."""

from __future__ import annotations

import asyncio
import io
import os
import re
import time
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from typing import BinaryIO

from rattler.platform import Platform
from rattler.repo_data import Gateway
from textual.pilot import Pilot
from textual.widgets import OptionList, Static
from textual.worker import Worker, WorkerState

from pixi_browse.tui import CondaMetadataTui

ANACONDA_CHANNELS_URL = "https://conda.anaconda.org/"
# The channel the app loads by default.
MAIN_CHANNEL = "conda-forge"
UPSTREAM_CHANNEL_URL = f"{ANACONDA_CHANNELS_URL}{MAIN_CHANNEL}/"
# A second real channel from the manifest, for channel switching.
BIOCONDA_CHANNEL = "bioconda"
# Mirrored, but without any repodata: loading it fails.
MISSING_CHANNEL = "missing"
TERMINAL_SIZE = (120, 40)
CHANNEL_PLATFORMS = (Platform("linux-64"), Platform("osx-arm64"), Platform("noarch"))

AppFactory = Callable[..., CondaMetadataTui]
GatewayFactory = Callable[..., Gateway]
PilotHook = Callable[[Pilot[None]], Awaitable[None]]
SnapCompare = Callable[..., bool]


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """``SimpleHTTPRequestHandler`` with single-range ``Range`` support.

    rattler reads ``.conda`` archives with HTTP range requests (it only fetches
    the zip central directory and the ``info`` payload), which the standard
    library handler does not implement.
    """

    protocol_version = "HTTP/1.1"
    _RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return None

    def end_headers(self) -> None:
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self) -> io.BytesIO | BinaryIO | None:
        range_header = self.headers.get("Range")
        path = self.translate_path(self.path)
        if range_header is None or os.path.isdir(path):
            return super().send_head()

        match = self._RANGE_PATTERN.fullmatch(range_header.strip())
        if match is None or not (match[1] or match[2]):
            self.send_error(HTTPStatus.RANGE_NOT_SATISFIABLE)
            return None

        try:
            with open(path, "rb") as file:
                data = file.read()
                modified = os.fstat(file.fileno()).st_mtime
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        size = len(data)
        if match[1]:
            start = int(match[1])
            end = int(match[2]) if match[2] else size - 1
        else:
            start = max(size - int(match[2]), 0)
            end = size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            self.send_response(HTTPStatus.RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        body = data[start : end + 1]
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Last-Modified", self.date_time_string(int(modified)))
        self.end_headers()
        return io.BytesIO(body)


async def wait_for_idle(pilot: Pilot[None], *, timeout: float = 30.0) -> None:
    """Wait until the app finished loading repodata and all workers are done.

    ``on_mount`` awaits the initial repodata load and the previews run in
    Textual workers, so a snapshot must wait for both before it is stable.
    Modal screens (query prompts, the who-needs loading screen, ...) may be on
    top of the main screen while waiting, so the widgets are looked up on the
    main screen rather than on whatever screen is active.
    """
    app = pilot.app
    assert isinstance(app, CondaMetadataTui)
    main_screen = app.screen_stack[0]
    deadline = time.monotonic() + timeout
    seen_workers: dict[int, Worker[object]] = {}
    while True:
        await pilot.pause()
        workers = list(app.workers)
        seen_workers.update((id(worker), worker) for worker in workers)
        sidebar = main_screen.query_one("#sidebar-list", OptionList)
        status = main_screen.query_one("#status", Static)
        if str(status.content).startswith("Failed to load repodata"):
            raise AssertionError(f"app failed to load repodata: {status.content}")
        # Poll instead of `workers.wait_for_complete()`: that raises
        # WorkerCancelled for workers cancelled by exclusive groups or resets.
        if not sidebar.disabled and all(worker.is_finished for worker in workers):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"app did not become idle within {timeout}s (status={status.content!r})"
            )
        await asyncio.sleep(0.02)
    # Flush UI updates posted by workers that finished just before the check.
    await pilot.pause()

    # The app swallows worker errors (``exit_on_error=False``); surface them
    # here so a test fails with the real exception instead of a snapshot of a
    # panel stuck on "Loading...".
    failed = [
        worker for worker in seen_workers.values() if worker.state == WorkerState.ERROR
    ]
    if failed:
        raise AssertionError(
            "app workers failed: "
            + "; ".join(f"{worker.group}: {worker.error!r}" for worker in failed)
        )


async def open_versions(pilot: Pilot[None], package_index: int) -> None:
    """Open the version list of the ``package_index``-th package and highlight
    its newest artifact so the main panel loads that artifact's details."""
    await wait_for_idle(pilot)
    await pilot.press(*(["j"] * package_index))
    await wait_for_idle(pilot)
    await pilot.press("enter")
    await wait_for_idle(pilot)
    # Row 0 is "< Back to packages", row 1 the first platform section.
    await pilot.press("j", "j")
    await wait_for_idle(pilot)


def notification_messages(app: CondaMetadataTui) -> list[str]:
    """The notifications the app currently shows, as ``title: message``."""
    return [
        f"{notification.title}: {notification.message}"
        for notification in app._notifications
    ]


async def type_text(pilot: Pilot[None], text: str) -> None:
    """Type ``text`` key by key, translating characters Textual names."""
    await pilot.press(*("space" if char == " " else char for char in text))
