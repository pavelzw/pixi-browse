"""Helpers shared by the test-suite: local channel server and pilot utilities."""

from __future__ import annotations

import asyncio
import inspect
import io
import os
import pickle
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, BinaryIO

import pytest
from pytest_textual_snapshot.plugin import (  # type: ignore[import-untyped]
    PseudoApp,
    PseudoConsole,
    SVGImageExtension,
    node_to_report_path,
)
from rattler.platform import Platform
from rattler.repo_data import Gateway
from rich.color import Color
from rich.console import Console
from rich.terminal_theme import TerminalTheme
from syrupy.assertion import SnapshotAssertion
from syrupy.data import Snapshot, SnapshotCollection
from syrupy.location import PyTestLocation
from textual.pilot import Pilot
from textual.screen import Screen
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
SnapComparePalettes = Callable[..., bool]

# The app draws in ANSI colors (its theme is `ansi-dark`), so the terminal
# palette alone decides how it ends up looking. Every screen is therefore
# snapshotted with two palettes, and with the very ones `pixi run demo` records
# the dark and the light demo with, so a snapshot shows what a real terminal
# with that theme shows.
DARK_PALETTE = "dark"
LIGHT_PALETTE = "light"


def vhs_theme(
    *,
    background: str,
    foreground: str,
    normal: Sequence[str],
    bright: Sequence[str],
) -> TerminalTheme:
    """A Rich export palette built from the hex colors of a VHS theme.

    ``normal`` and ``bright`` are the eight ANSI colors in the order VHS' theme
    database lists them: black, red, green, yellow, blue, magenta, cyan, white
    (https://github.com/charmbracelet/vhs/blob/main/themes.json).
    """

    def triplet(color: str) -> tuple[int, int, int]:
        parsed = Color.parse(color).triplet
        assert parsed is not None, f"Not a hex color: {color}"
        return parsed

    return TerminalTheme(
        triplet(background),
        triplet(foreground),
        [triplet(color) for color in normal],
        [triplet(color) for color in bright],
    )


SVG_PALETTES: dict[str, TerminalTheme] = {
    # `rose-pine-moon`, set by `.github/assets/demo-dark.tape`.
    DARK_PALETTE: vhs_theme(
        background="#232136",
        foreground="#e0def4",
        normal=(
            "#393552",
            "#eb6f92",
            "#9ccfd8",
            "#f6c177",
            "#3e8fb0",
            "#c4a7e7",
            "#ea9a97",
            "#e0def4",
        ),
        bright=(
            "#6e6a86",
            "#eb6f92",
            "#9ccfd8",
            "#f6c177",
            "#3e8fb0",
            "#c4a7e7",
            "#ea9a97",
            "#e0def4",
        ),
    ),
    # `rose-pine-dawn`, set by `.github/assets/demo-light.tape`.
    LIGHT_PALETTE: vhs_theme(
        background="#faf4ed",
        foreground="#575279",
        normal=(
            "#f2e9e1",
            "#b4637a",
            "#56949f",
            "#ea9d34",
            "#286983",
            "#907aa9",
            "#d7827e",
            "#575279",
        ),
        bright=(
            "#9893a5",
            "#b4637a",
            "#56949f",
            "#ea9d34",
            "#286983",
            "#907aa9",
            "#d7827e",
            "#575279",
        ),
    ),
}


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


class PaletteScreenshotApp(CondaMetadataTui):
    """The app under test, screenshotted with every palette in a single run.

    ``pytest-textual-snapshot`` takes its screenshot through
    ``App.export_screenshot``, which records the screen and exports it as an SVG
    with Rich's dark default palette. Recording is the expensive half -- it
    needs the whole app run -- while turning the recording into an SVG only maps
    the recorded ANSI colors to a palette's hex colors. This override therefore
    exports one SVG per palette from a single recording and keeps them all in
    ``palette_screenshots``.
    """

    # Textual titles a window after its app class, and that title is drawn into
    # the screenshot, so keep the name of the app under test.
    TITLE = CondaMetadataTui.__name__

    palette_screenshots: dict[str, str]
    """The SVGs of the last screenshot, one per palette in ``SVG_PALETTES``."""

    def export_screenshot(
        self, *, title: str | None = None, simplify: bool = False
    ) -> str:
        # Mirrors `App.export_screenshot` of Textual 8, except that the recorded
        # console is exported once per palette instead of once in total.
        assert self._driver is not None, "App must be running"
        width, height = self.size
        console = Console(
            width=width,
            height=height,
            file=io.StringIO(),
            force_terminal=True,
            color_system="truecolor",
            record=True,
            legacy_windows=False,
            safe_box=False,
        )
        console.print(
            self.screen._compositor.render_update(
                full=True, screen_stack=self._background_screens, simplify=simplify
            )
        )
        self.palette_screenshots = {
            palette: console.export_svg(
                title=title or self.title, theme=theme, clear=False
            )
            for palette, theme in SVG_PALETTES.items()
        }
        return self.palette_screenshots[DARK_PALETTE]


class PaletteSVGImageExtension(SVGImageExtension):  # type: ignore[misc]
    """One SVG file per palette, both next to each other.

    The palette is passed as syrupy's snapshot index (``snapshot(name=palette)``
    in the ``snap_compare_palettes`` fixture), which syrupy would spell
    ``<test>[<palette>].svg``. Only the file name is spelled differently here,
    as ``<test>.<palette>.svg``, so that a test's palettes read as variants of
    one screen and sort next to each other; syrupy still manages them like any
    other snapshot (writing new ones, updating changed ones and deleting the
    snapshots of deleted tests).
    """

    @classmethod
    def get_file_basename(
        cls, *, test_location: PyTestLocation, index: str | int
    ) -> str:
        palette_name = super().get_file_basename(test_location=test_location, index=0)
        return f"{palette_name}.{index}"

    def read_snapshot_collection(self, *, snapshot_location: str) -> SnapshotCollection:
        # Undo `get_file_basename`: syrupy takes the name of a single-file
        # snapshot from its file name, and has to arrive back at the name the
        # comparison used, or it reports the snapshot as belonging to no test.
        test_name, _, palette = Path(snapshot_location).stem.rpartition(".")
        collection = SnapshotCollection(location=snapshot_location)
        collection.add(Snapshot(name=f"{test_name}[{palette}]"))
        return collection


def report_palette_comparison(
    node: pytest.Function,
    snapshot: SnapshotAssertion,
    palette: str,
    svg: str,
    *,
    matches: bool,
) -> None:
    """List a palette's comparison in ``snapshot_report.html``.

    ``pytest-textual-snapshot`` builds that report at the end of the session out
    of one pickled comparison per test; pickling one comparison per palette the
    same way puts the palettes of a screen next to each other in the report.
    """
    execution = snapshot.executions.get(snapshot.num_executions - 1)
    console = Console(legacy_windows=False, force_terminal=True)
    full_path, line_number, name = node.reportinfo()
    comparison = (
        matches,
        str(snapshot),
        svg,
        PseudoApp(PseudoConsole(console.legacy_windows, console.size)),
        full_path,
        line_number,
        f"{name} ({palette})",
        inspect.getdoc(node.function) or "",
        "",
        execution is not None and execution.final_data is not None,
    )
    report_path = node_to_report_path(node)
    report_path.with_name(f"{report_path.name}_{palette}").write_bytes(
        pickle.dumps(comparison)
    )


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


async def wait_for_screen(
    # ``Screen`` is invariant in its result type, so only ``Any`` accepts every
    # modal of the app here.
    pilot: Pilot[None],
    screen_type: type[Screen[Any]],
    *,
    timeout: float = 5.0,
) -> None:
    """Wait until a screen of ``screen_type`` is on top of the screen stack.

    The app opens some modals via ``call_after_refresh`` (the file action
    screen, the MatchSpec prompt for a selected dependency), so a test that
    presses the key has to wait for the deferred push instead of assuming it
    already happened.
    """
    deadline = time.monotonic() + timeout
    while not isinstance(pilot.app.screen, screen_type):
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"{screen_type.__name__} did not open within {timeout}s "
                f"(top screen: {type(pilot.app.screen).__name__})"
            )
        await pilot.pause()
    await pilot.pause()


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
