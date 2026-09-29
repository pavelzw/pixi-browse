"""Snapshot tests of the file actions offered for files of real archives."""

from __future__ import annotations

import asyncio

from textual.events import MouseDown, MouseMove, MouseUp
from textual.pilot import Pilot

from pixi_browse.tui import FileActionScreen, FilePreviewScreen
from tests.helpers import (
    TERMINAL_SIZE,
    AppFactory,
    SnapComparePalettes,
    open_versions,
    wait_for_idle,
    wait_for_screen,
)


def test_enter_on_package_file_opens_file_actions(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Files from ``paths.json`` carry their SHA256."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "enter")
        await wait_for_screen(pilot, FileActionScreen)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_enter_on_info_file_opens_file_actions(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Files streamed from ``info/`` only know their size."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "]", "enter")
        await wait_for_screen(pilot, FileActionScreen)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_clicking_file_opens_file_actions(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Clicking a file row opens the same file action screen as ``Enter``."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.click("#detail-option-list-2", offset=(2, 0))
        await wait_for_screen(pilot, FileActionScreen)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_enter_on_symlink_does_nothing(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``libzlib`` ships ``lib/libz.so`` as a symlink, which has no actions."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("3", "enter")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_file_actions_escape_returns_to_details(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``Escape`` closes the file action screen and leaves the file pane active."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("3", "enter")
        await wait_for_screen(pilot, FileActionScreen)
        await pilot.press("escape")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_preview_python_file_uses_syntax_highlighting(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
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

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_preview_escape_returns_to_details(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
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

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_preview_text_can_be_selected_and_copied(make_app: AppFactory) -> None:
    """A drag in a package file preview selects text for ``Ctrl+C``."""

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_versions(pilot, package_index=1)
            await pilot.press("3", "enter")
            await wait_for_screen(pilot, FileActionScreen)
            await pilot.press("enter")
            await wait_for_screen(pilot, FilePreviewScreen)
            await wait_for_idle(pilot)

            body = app.screen.query_one("#file-preview-body")
            x, y = body.region.offset
            for event_type, offset in [
                (MouseDown, 0),
                (MouseMove, 20),
                (MouseUp, 20),
            ]:
                app.post_message(
                    event_type(None, x + offset, y, 0, 0, 1, False, False, False)
                )
                await pilot.pause()

            selected = app.screen.get_selected_text()
            assert selected
            await pilot.press("ctrl+c")
            assert app.clipboard == selected

    asyncio.run(run())


def test_download_path_screen_rejects_empty_destination(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
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

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_d_on_section_row_warns(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``d`` on a platform section row warns that a specific artifact must be
    selected."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("k", "d")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )
