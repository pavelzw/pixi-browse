"""Snapshot tests of comparing two real ``libzlib`` artifacts."""

from __future__ import annotations

import asyncio

from textual.pilot import Pilot

from tests.helpers import (
    TERMINAL_SIZE,
    AppFactory,
    SnapCompare,
    open_versions,
    wait_for_idle,
)


async def open_compare_screen(pilot: Pilot[None]) -> None:
    """Compare ``libzlib 1.3.2`` (compare A) with ``libzlib 1.3.1`` on
    linux-64; the screen orders the older build on the left."""
    await open_versions(pilot, package_index=0)
    await pilot.press("C", "j")
    await wait_for_idle(pilot)
    await pilot.press("C")
    await wait_for_idle(pilot)


def test_compare_key_stores_first_selection(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("C")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_key_rejects_the_same_artifact_twice(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("C", "C")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_key_on_section_row_does_nothing(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("k", "C")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_shows_metadata_diff(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    assert snap_compare(
        make_app(), run_before=open_compare_screen, terminal_size=TERMINAL_SIZE
    )


def test_compare_screen_tab_activates_dependencies(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("tab", "]", "]")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_shift_tab_activates_files(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("shift+tab", "j")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_swap_sides(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("x")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_info_tab_marks_unresolved_rows(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Info files only carry sizes, so equal-sized files are unknown (``?``)
    until their hashes are compared."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("3", "]")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_enter_on_info_file_resolves_hashes(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Selecting an unresolved info file (``paths.json``) hashes both
    archives' copies, marks the row and offers the file actions for both
    sides."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("3", "]", "j", "enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_enter_on_symlink_row_warns(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("3", "enter")
        await pilot.pause()
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_enter_on_left_only_file_offers_left_actions(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("3", "j", "enter")
        await pilot.pause()
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_rejects_binary_preview(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("3", "j", "enter")
        await pilot.pause()
        await pilot.pause()
        # "Preview left" of the shared library.
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_escape_returns_to_versions(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Leaving the compare screen also forgets compare A."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("escape")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_q_exits_app(make_app: AppFactory) -> None:
    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_compare_screen(pilot)
            assert app.return_code is None
            await pilot.press("q")
            await pilot.pause()
            assert app.return_code == 0

    asyncio.run(run())
