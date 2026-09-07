"""Snapshot tests of comparing two real ``libzlib`` artifacts."""

from __future__ import annotations

import asyncio

import pytest
from textual.pilot import Pilot

from pixi_browse.tui import DIFF_VIEW_AVAILABLE, FileActionScreen, FileDiffScreen
from tests.helpers import (
    TERMINAL_SIZE,
    AppFactory,
    SnapCompare,
    notification_messages,
    open_versions,
    wait_for_idle,
    wait_for_screen,
)


async def open_compare_screen(pilot: Pilot[None]) -> None:
    """Compare ``libzlib 1.3.2`` (compare A) with ``libzlib 1.3.1`` on
    linux-64; the screen orders the older build on the left."""
    await open_versions(pilot, package_index=0)
    await pilot.press("C", "j")
    await wait_for_idle(pilot)
    await pilot.press("C")
    await wait_for_idle(pilot)


async def open_polars_compare_screen(pilot: Pilot[None]) -> None:
    """Compare ``polars 1.44.1`` (compare A) with ``polars 1.44.0`` on noarch;
    the screen orders the older build on the left."""
    await open_versions(pilot, package_index=2)
    await pilot.press("C", "j")
    await wait_for_idle(pilot)
    await pilot.press("C")
    await wait_for_idle(pilot)


# Row of ``site-packages/polars/functions/lit.py`` in the compare file list,
# which follows the order of the older build's ``paths.json``.
POLARS_LIT_PY_ROW = 91


def test_compare_key_stores_first_selection(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``C`` on an artifact stores it as compare A, notifies, and highlights the
    compare hint in the footer."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("C")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_key_rejects_the_same_artifact_twice(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``C`` twice on the same artifact warns that compare B must differ."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("C", "C")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_key_on_section_row_does_nothing(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``C`` on a platform section row stores nothing."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("k", "C")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_shows_metadata_diff(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """The compare screen lists metadata side by side with the older build on the
    left."""

    assert snap_compare(
        make_app(), run_before=open_compare_screen, terminal_size=TERMINAL_SIZE
    )


def test_compare_screen_tab_activates_dependencies(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Tab`` activates the dependency pane; ``]`` twice switches it to the run
    exports."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("tab", "]", "]")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_shift_tab_activates_files(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Shift+Tab`` wraps to the file pane, where ``j`` moves the highlight."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("shift+tab", "j")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_swap_sides(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``x`` swaps left and right, including the title colors and the file
    markers."""

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
    """``Enter`` on a symlink row explains that symlinks cannot be previewed or
    downloaded."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("3", "enter")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_enter_on_left_only_file_offers_left_actions(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Enter`` on a file only the left build has offers preview and download for
    the left side only."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("3", "j", "enter")
        await wait_for_screen(pilot, FileActionScreen)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_compare_screen_rejects_binary_preview(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Previewing a shared library shows the binary-file notice instead of its
    contents."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_compare_screen(pilot)
        await pilot.press("3", "j", "enter")
        await wait_for_screen(pilot, FileActionScreen)
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
    """``q`` on the compare screen quits the whole app."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_compare_screen(pilot)
            assert app.return_code is None
            await pilot.press("q")
            await pilot.pause()
            assert app.return_code == 0

    asyncio.run(run())


@pytest.mark.skipif(
    not DIFF_VIEW_AVAILABLE, reason="needs the optional textual-diff-view package"
)
def test_compare_screen_diff_of_python_file(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Diff left / right`` on a changed Python file (``polars/functions/lit.py``)
    opens the diff of both archives' copies with syntax highlighting."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_polars_compare_screen(pilot)
        await pilot.press("3", *(["j"] * POLARS_LIT_PY_ROW), "enter")
        await wait_for_screen(pilot, FileActionScreen)
        # "Diff left / right" is the first action of a changed two-sided row.
        await pilot.press("enter")
        await wait_for_screen(pilot, FileDiffScreen)
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


@pytest.mark.skipif(
    not DIFF_VIEW_AVAILABLE, reason="needs the optional textual-diff-view package"
)
def test_compare_screen_diff_escape_returns_to_compare(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Escape`` closes the diff and returns to the compare screen."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_polars_compare_screen(pilot)
        await pilot.press("3", *(["j"] * POLARS_LIT_PY_ROW), "enter")
        await wait_for_screen(pilot, FileActionScreen)
        await pilot.press("enter")
        await wait_for_screen(pilot, FileDiffScreen)
        await pilot.press("escape")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


@pytest.mark.skipif(
    DIFF_VIEW_AVAILABLE, reason="textual-diff-view is installed in this environment"
)
def test_compare_screen_diff_without_textual_diff_view_explains_install(
    make_app: AppFactory,
) -> None:
    """Without the optional ``textual-diff-view`` package the diff action still
    shows up, and picking it explains how to install the package."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_polars_compare_screen(pilot)
            await pilot.press("3", *(["j"] * POLARS_LIT_PY_ROW), "enter")
            await wait_for_screen(pilot, FileActionScreen)
            await pilot.press("enter")
            await wait_for_idle(pilot)
            assert not isinstance(app.screen, FileDiffScreen)
            messages = notification_messages(app)
            assert any(
                message.startswith("Diff: Diffing files needs the optional")
                for message in messages
            ), messages

    asyncio.run(run())
