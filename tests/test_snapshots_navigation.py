"""Snapshot tests of keyboard and mouse navigation in the real TUI.

Pane focus, sidebar movement, the detail sections of the versions view and
their tabs are all driven through the real ``CondaMetadataTui`` against the
offline channel; see ``test_snapshots.py`` for how snapshots are reviewed.
"""

from __future__ import annotations

import pytest
from textual.events import MouseDown, MouseMove, MouseUp
from textual.pilot import Pilot

from tests.helpers import (
    NARROW_TERMINAL_SIZE,
    TERMINAL_SIZE,
    AppFactory,
    SnapComparePalettes,
    open_versions,
    wait_for_idle,
)


@pytest.mark.parametrize("close", [False, True], ids=["select", "exit"])
def test_metadata_text_selection(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory, close: bool
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("1")
        await pilot.pause()
        body = pilot.app.query_one("#detail-body-0")
        x, y = body.region.offset
        # Pilot's mouse helpers bypass App.on_event; send real input events so
        # the per-gesture metadata selection gate is exercised as well.
        for event_type, offset in [(MouseDown, 0), (MouseMove, 20), (MouseUp, 20)]:
            pilot.app.post_message(
                event_type(None, x + offset, y, 0, 0, 1, False, False, False)
            )
            await pilot.pause()
        await pilot.pause()
        selected = pilot.app.screen.get_selected_text()
        assert selected
        await pilot.press("ctrl+c")
        assert pilot.app.clipboard == selected
        if close:
            await pilot.press("escape")
            await pilot.pause()
            assert not pilot.app.screen.get_selected_text()
            status = pilot.app.query_one("#status")
            x, y = status.region.offset
            for event_type, offset in [(MouseDown, 0), (MouseMove, 10), (MouseUp, 10)]:
                pilot.app.post_message(
                    event_type(None, x + offset, y, 0, 0, 1, False, False, False)
                )
                await pilot.pause()
            assert not pilot.app.ALLOW_SELECT
            assert not pilot.app.screen.get_selected_text()
            await pilot.click("#detail-body-0")

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


@pytest.mark.parametrize("key", ["l", "1"])
def test_key_focuses_main_panel_from_sidebar(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory, key: str
) -> None:
    """``l`` and ``1`` move the selected pane to the details panel."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press(key)
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


@pytest.mark.parametrize("key", ["escape", "0", "h"])
def test_key_returns_focus_to_sidebar_from_main_panel(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory, key: str
) -> None:
    """``Escape``, ``0`` and ``h`` move the selected pane back to the sidebar."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("l")
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_j_moves_sidebar_highlight_and_previews_package(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Two ``j`` presses highlight ``six`` and preview its ``.conda`` and
    legacy ``.tar.bz2`` builds."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("j", "j")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


@pytest.mark.parametrize("keys", [("G",), ("ctrl+d",)], ids=["G", "ctrl+d"])
def test_jump_and_page_down_highlight_last_package(
    snap_compare_palettes: SnapComparePalettes,
    make_app: AppFactory,
    keys: tuple[str, ...],
) -> None:
    """``G`` and ``Ctrl+d`` jump to the last package and preview it."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press(*keys)
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


@pytest.mark.parametrize("keys", [("g", "g"), ("ctrl+u",)], ids=["gg", "ctrl+u"])
def test_jump_and_page_up_return_to_first_package(
    snap_compare_palettes: SnapComparePalettes,
    make_app: AppFactory,
    keys: tuple[str, ...],
) -> None:
    """``gg`` and ``Ctrl+u`` return from the last package to the first."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("G")
        await wait_for_idle(pilot)
        await pilot.press(*keys)
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_single_g_does_not_jump(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``g`` alone only arms the ``gg`` chord; the highlight stays put."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("G", "g")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_clicking_main_panel_focuses_it(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Clicking into the details panel makes it the selected pane."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.click("#main-placeholder")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_clicking_sidebar_panel_focuses_package_list(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """A click anywhere in the sidebar (here: the status line) focuses the
    package list again."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("l")
        await pilot.pause()
        await pilot.click("#status")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_clicking_package_opens_its_versions(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Selecting a package with the mouse opens its versions and keeps the
    sidebar focused."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        # The second option row is ``pixi-browse``.
        await pilot.click("#sidebar-list", offset=(2, 1))
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_escape_returns_from_versions_to_packages(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Going back restores the previously highlighted package."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("escape")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_back_row_previews_the_package_again(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Highlighting ``< Back to packages`` shows the package preview again."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("g", "g")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_highlighting_platform_section_shows_placeholder(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Highlighting a platform section row shows the collapse/expand hint instead
    of artifact details."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("k")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_enter_on_platform_section_collapses_it(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``Enter`` on a platform section collapses its artifacts and flips the
    marker."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("k", "enter")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_enter_on_version_entry_focuses_main_panel(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Selecting an entry with the keyboard hands focus to the details."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_clicking_version_entry_keeps_sidebar_focused(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Selecting an artifact with the mouse loads its details but keeps the
    sidebar focused."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        # Row 3 is the second artifact (0.0.13) of the noarch section.
        await pilot.click("#sidebar-list", offset=(2, 3))
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


@pytest.mark.parametrize("key", ["1", "2", "3"])
def test_numeric_shortcut_activates_section_and_focuses_main_panel(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory, key: str
) -> None:
    """``1``, ``2`` and ``3`` activate the metadata, dependency and file section
    and focus the details."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press(key)
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_zero_returns_focus_to_sidebar_in_versions_view(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``0`` returns focus to the version list after a section shortcut."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("2", "0")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_shift_tab_cycles_sections_backwards(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``Shift+Tab`` from the metadata section wraps around to the file section."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("l", "shift+tab")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


@pytest.mark.parametrize("key", ["tab", "shift+tab"])
def test_tab_in_sidebar_focuses_the_active_section(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory, key: str
) -> None:
    """``Tab`` and ``Shift+Tab`` move from the version list to the details
    panel, at the section that was active there (``[2]``), instead of cycling
    past it."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("2", "0", key)
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_tab_cycles_sections_after_entering_from_sidebar(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Once ``Tab`` has moved to the details panel, the next ``Tab`` cycles on
    from the active section ``[2]`` to the file section ``[3]``."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("2", "0", "tab", "tab")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_bracket_in_sidebar_does_not_cycle_tabs(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``]`` and ``[`` are ignored while the sidebar is the selected pane."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("]", "[")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_bracket_switches_metadata_to_repodata_patches_tab(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """The fixture repodata is generated from the packages themselves, so the
    patches tab reports none."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("l", "]")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_metadata_tab_persists_across_artifacts(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """The repodata patches tab stays selected when another artifact is
    highlighted."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("l", "]", "h", "j")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_bracket_switches_dependency_tab_to_constraints(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``]`` in the dependency section switches past extra dependencies to the constraints tab."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("2", "]", "]")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_narrow_window_clips_dependency_tabs_to_the_active_tab(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """In a window too narrow for the whole tab strip, ``]`` clips the tabs from
    the left so that the constraints tab it switched to is readable."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("2", "]", "]")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=NARROW_TERMINAL_SIZE
    )


def test_shrinking_the_window_clips_the_tabs_of_the_active_section(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Shrinking the terminal re-clips the tab strips to the width the sections
    are left with, so the run exports tab stays visible in the narrow window."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("2", "]", "]", "]")
        await pilot.resize_terminal(*NARROW_TERMINAL_SIZE)
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_left_bracket_wraps_dependency_tab_to_run_exports(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``[`` in the dependency section wraps around to the run exports tab."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("2", "[")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_bracket_switches_file_tab_to_info(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``]`` in the file section switches from ``pkg/`` to the ``info/`` files."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "]")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_j_and_shift_g_move_highlight_in_file_list(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``j``, ``G`` and ``k`` move the highlight in the file list; it ends on the
    second-to-last file."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "j", "j", "G", "k")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_clicking_metadata_body_activates_metadata_section(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Clicking the metadata text activates that section while the file section
    was active."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3")
        await pilot.pause()
        await pilot.click("#detail-body-0")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_resize_rerenders_versions_view(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Shrinking the terminal re-lays out the version rows and re-renders the
    highlighted artifact from the cache."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.resize_terminal(90, 30)
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


@pytest.mark.parametrize("package_index", [0, 1], ids=["empty", "grouped"])
def test_extra_depends_tab(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory, package_index: int
) -> None:
    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=package_index)
        await pilot.press("2", "]")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )
