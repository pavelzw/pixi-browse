"""Snapshot tests of the file actions offered for files of real archives."""

from __future__ import annotations

import pytest
from textual.pilot import Pilot

from pixi_browse.tui import FileActionScreen
from tests.helpers import (
    TERMINAL_SIZE,
    AppFactory,
    SnapCompare,
    open_versions,
    wait_for_idle,
    wait_for_screen,
)


def test_enter_on_package_file_opens_file_actions(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Files from ``paths.json`` carry their SHA256."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "enter")
        await wait_for_screen(pilot, FileActionScreen)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_enter_on_info_file_opens_file_actions(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Files streamed from ``info/`` only know their size."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "]", "enter")
        await wait_for_screen(pilot, FileActionScreen)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_clicking_file_opens_file_actions(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Clicking a file row opens the same file action screen as ``Enter``."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.click("#detail-option-list-2", offset=(2, 0))
        await wait_for_screen(pilot, FileActionScreen)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_enter_on_symlink_does_nothing(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``libzlib`` ships ``lib/libz.so`` as a symlink, which has no actions."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("3", "enter")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_file_actions_escape_returns_to_details(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Escape`` closes the file action screen and leaves the file pane active."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "enter")
        await wait_for_screen(pilot, FileActionScreen)
        await pilot.press("escape")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_preview_python_file_uses_syntax_highlighting(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Previewing a ``.py`` file renders it with Python syntax highlighting."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "enter")
        await wait_for_screen(pilot, FileActionScreen)
        await pilot.press("enter")
        await wait_for_idle(pilot)
        await pilot.press("j", "j", "j")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_preview_escape_returns_to_details(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Escape`` closes the preview and returns to the details view."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "enter")
        await wait_for_screen(pilot, FileActionScreen)
        await pilot.press("enter")
        await wait_for_idle(pilot)
        await pilot.press("escape")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_download_path_screen_rejects_empty_destination(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Submitting an empty destination in the download prompt shows an inline
    error."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "]", "enter")
        await wait_for_screen(pilot, FileActionScreen)
        # "Download as file": the default destination is selected on focus.
        await pilot.press("down", "enter")
        await pilot.pause()
        await pilot.press("backspace", "enter")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_d_on_section_row_warns(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``d`` on a platform section row warns that a specific artifact must be
    selected."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("k", "d")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize(
    "keys",
    [("G",), ("ctrl+d",), ("ctrl+d", "k"), ("end", "ctrl+u"), ("G", "g")],
    ids=["G", "ctrl+d", "ctrl+d-k", "end-ctrl+u", "G-g"],
)
def test_preview_scroll_keys(
    snap_compare: SnapCompare, make_app: AppFactory, keys: tuple[str, ...]
) -> None:
    """The preview of ``pixi_browse/__main__.py`` is longer than the dialog:
    ``G``/``end`` jump to the end, ``g`` back to the top, ``ctrl+d``/``ctrl+u``
    page and ``k`` scrolls one line up."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        # The second pkg/ file is site-packages/pixi_browse/__main__.py.
        await pilot.press("3", "j", "enter")
        await wait_for_screen(pilot, FileActionScreen)
        await pilot.press("enter")
        await wait_for_idle(pilot)
        await pilot.press(*keys)
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_download_path_escape_cancels_download(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Escape`` on the destination prompt downloads nothing and returns to the
    details."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "]", "enter")
        await wait_for_screen(pilot, FileActionScreen)
        await pilot.press("down", "enter")
        await pilot.pause()
        await pilot.press("escape")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)
