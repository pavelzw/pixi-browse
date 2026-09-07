"""Snapshot tests of the file actions offered for files of real archives."""

from __future__ import annotations

from textual.pilot import Pilot

from tests.helpers import (
    TERMINAL_SIZE,
    AppFactory,
    SnapCompare,
    open_versions,
    wait_for_idle,
)


def test_enter_on_package_file_opens_file_actions(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Files from ``paths.json`` carry their SHA256."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "enter")
        await pilot.pause()
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_enter_on_info_file_opens_file_actions(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Files streamed from ``info/`` only know their size."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "]", "enter")
        await pilot.pause()
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_clicking_file_opens_file_actions(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.click("#detail-option-list-2", offset=(2, 0))
        await pilot.pause()
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_enter_on_symlink_does_nothing(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``libzlib`` ships ``lib/libz.so`` as a symlink, which has no actions."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("3", "enter")
        await pilot.pause()
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_file_actions_escape_returns_to_details(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "enter")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_preview_python_file_uses_syntax_highlighting(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "enter")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("enter")
        await wait_for_idle(pilot)
        await pilot.press("j", "j", "j")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_preview_escape_returns_to_details(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "enter")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("enter")
        await wait_for_idle(pilot)
        await pilot.press("escape")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_download_path_screen_rejects_empty_destination(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "]", "enter")
        await pilot.pause()
        await pilot.pause()
        # "Download as file": the default destination is selected on focus.
        await pilot.press("down", "enter")
        await pilot.pause()
        await pilot.press("backspace", "enter")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_d_on_section_row_warns(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("k", "d")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)
