"""Snapshot tests of the ``/`` search inside the detail lists.

The package list search lives in ``test_snapshots.py``; these tests cover the
same search in the dependency and file lists of the version details and in the
compare screen's file list.
"""

from __future__ import annotations

import asyncio

from textual.pilot import Pilot

from pixi_browse.tui import FileActionScreen, VersionDetailsView
from tests.helpers import (
    TERMINAL_SIZE,
    AppFactory,
    SnapCompare,
    open_versions,
    type_text,
    wait_for_idle,
    wait_for_screen,
)
from tests.test_snapshots_compare import open_polars_compare_screen


def test_search_narrows_the_package_file_list(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``/`` in the file section of ``polars`` ranks the files matching
    ``lit.py`` first and counts the matches in the tab labels."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=2)
        # The file section, then search it.
        await pilot.press("3", "slash")
        await type_text(pilot, "lit.py")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_search_without_matches_reports_it(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """A query no file matches empties the list instead of hiding the search."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=2)
        await pilot.press("3", "slash")
        await type_text(pilot, "qqq")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_search_escape_restores_the_full_file_list(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Escape`` leaves the search and shows every file again."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=2)
        await pilot.press("3", "slash")
        await type_text(pilot, "lit.py")
        await wait_for_idle(pilot)
        await pilot.press("escape")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_search_backspace_widens_the_file_list(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Backspace`` shortens the query and re-ranks the matches."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=2)
        await pilot.press("3", "slash")
        await type_text(pilot, "lit.py")
        await wait_for_idle(pilot)
        await pilot.press("backspace", "backspace", "backspace")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_enter_on_a_searched_file_opens_its_file_actions(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """The file actions belong to the highlighted match, not to the row that
    sat at that position before the search."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=2)
        await pilot.press("3", "slash")
        await type_text(pilot, "lit.py")
        await wait_for_idle(pilot)
        await pilot.press("enter")
        await wait_for_screen(pilot, FileActionScreen)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_search_narrows_the_info_file_list(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """The search follows the active tab, so it narrows ``info/`` files too."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        # The file section, its info tab, then search it.
        await pilot.press("3", "]", "slash")
        await type_text(pilot, "json")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_search_narrows_the_dependency_list(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``/`` in the dependency section of ``pixi-browse`` keeps the matching
    MatchSpecs, which ``Enter`` can still query."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        # The dependency section, then search it.
        await pilot.press("2", "slash")
        await type_text(pilot, "ratt")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_search_narrows_the_compare_file_list(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``/`` on the compare screen narrows the side-by-side file list and shows
    the query in its footer."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_polars_compare_screen(pilot)
        await pilot.press("3", "slash")
        await type_text(pilot, "lit.py")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_search_does_not_start_on_a_section_without_a_list(
    make_app: AppFactory,
) -> None:
    """The metadata section has nothing to search, so ``/`` stays inert there
    instead of swallowing the following keys."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_versions(pilot, package_index=1)
            await pilot.press("1", "slash")
            await wait_for_idle(pilot)

            assert app._list_search_mode is False
            assert app._filter_mode is False

            # The file section can be searched, so ``/`` starts there.
            await pilot.press("3", "slash")
            await type_text(pilot, "about")
            await wait_for_idle(pilot)

            assert app._list_search_mode is True
            assert app._list_search_query == "about"

    asyncio.run(run())


def test_leaving_the_compare_screen_ends_its_search(make_app: AppFactory) -> None:
    """The compare screen's search stays with that screen: the versions view
    behind it keeps its complete lists."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_polars_compare_screen(pilot)
            await pilot.press("3", "slash")
            await type_text(pilot, "lit.py")
            await wait_for_idle(pilot)
            assert app._list_search_mode is True

            # The first escape leaves the search, the second the screen.
            await pilot.press("escape")
            assert app._list_search_mode is False
            await pilot.press("escape")
            await wait_for_idle(pilot)

            assert app._list_search_query == ""
            details_view = app.query_one("#version-details-view", VersionDetailsView)
            assert details_view._filter_query is None

    asyncio.run(run())
