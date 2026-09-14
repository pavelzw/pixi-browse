"""Snapshot tests of the TUI against the offline real-data channel.

Each test drives the real ``CondaMetadataTui`` (real gateway, real package
archives, real rendering) and compares an SVG screenshot with the accepted
snapshot in ``tests/__snapshots__/test_snapshots/``.

Review a failure with the generated ``snapshot_report.html``; accept intended
changes with ``pixi run snapshot-update``.

The ``test_snapshots_*.py`` modules next to this one cover navigation, the
query prompts, the compare screen and the file actions the same way.
"""

from __future__ import annotations

from time import monotonic

from rattler.config import Config
from textual.pilot import Pilot

from pixi_browse.tui.widgets import RepodataLoadingScreen
from tests.helpers import (
    MISSING_CHANNEL,
    TERMINAL_SIZE,
    AppFactory,
    SnapComparePalettes,
    open_versions,
    type_text,
    wait_for_idle,
    wait_for_screen,
    wait_until,
)


def test_packages_view_lists_channel_packages(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Startup: the sidebar lists the packages and previews the first one."""
    assert snap_compare_palettes(
        make_app(), run_before=wait_for_idle, terminal_size=TERMINAL_SIZE
    )


async def loading_screen(pilot: Pilot[None]) -> RepodataLoadingScreen:
    """The startup loading screen, once it is on top."""
    await wait_for_screen(pilot, RepodataLoadingScreen)
    screen = pilot.app.screen
    assert isinstance(screen, RepodataLoadingScreen)
    return screen


def test_startup_loading_screen_reports_progress(
    snap_compare_palettes: SnapComparePalettes,
    make_app: AppFactory,
    stalled_linux64_config: Config,
) -> None:
    """Startup: while ``linux-64`` is still downloading, the loading screen
    lists the platforms found so far and the pending one, and counts the
    probed subdirs. The elapsed time is wall-clock time, pinned to 31s for the
    screenshot."""

    async def run_before(pilot: Pilot[None]) -> None:
        screen = await loading_screen(pilot)
        await wait_until(
            pilot,
            lambda: (
                screen.progress is not None
                and screen.progress.probes_completed == screen.progress.probes_total - 1
            ),
            what="every probe but linux-64 to finish",
        )
        screen.started_at = monotonic() - 31
        screen.refresh_elapsed()

    assert snap_compare_palettes(
        make_app(config=stalled_linux64_config),
        run_before=run_before,
        terminal_size=TERMINAL_SIZE,
    )


def test_startup_failure_stays_on_loading_screen(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Starting with a channel that has no repodata: the loading screen shows
    the error and offers to pick other channels."""

    async def run_before(pilot: Pilot[None]) -> None:
        screen = await loading_screen(pilot)
        await wait_until(pilot, lambda: screen.failed, what="the load to fail")

    assert snap_compare_palettes(
        make_app(default_channels=(MISSING_CHANNEL,)),
        run_before=run_before,
        terminal_size=TERMINAL_SIZE,
    )


def test_versions_view_shows_real_artifact_details(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Opening ``pixi-browse`` shows metadata, dependencies and files read
    from the real ``.conda`` archive."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_versions_view_marks_prefix_replacement_files(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``zlib`` ships ``lib/pkgconfig/zlib.pc`` with the build prefix baked in;
    the file list marks it as needing text prefix replacement."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=4)
        # Focus the main panel and activate the file section so all files show.
        await pilot.press("l", "3")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_versions_view_groups_subdirs_by_latest_version(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``libzlib`` has linux-64 and osx-arm64 builds; the highlighted 1.3.2
    build shows its run exports and symlinked files."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        # Focus the main panel and switch the dependency section to run exports.
        await pilot.press("l", "tab", "[")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_filter_narrows_package_list(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Typing ``six`` into the ``/`` search narrows the package list to the
    packages containing it and previews the best match."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("slash")
        await type_text(pilot, "six")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_matchspec_query_filters_records(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """A MatchSpec that matches a single package opens its versions, limited to
    the matching builds."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("m")
        await pilot.pause()
        await type_text(pilot, "libzlib >=1.3.2")
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_matchspec_query_lists_matching_packages(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """A query matching ``libzlib`` and ``zlib`` stays in the package list and
    names the MatchSpec in the sidebar heading."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("m")
        await pilot.pause()
        await type_text(pilot, "*zlib*")
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_whoneeds_query_lists_dependents(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``zlib`` depends on ``libzlib``; the who-needs scan runs against the
    complete repodata of the fixture channel."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("w")
        await pilot.pause()
        await type_text(pilot, "libzlib")
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_platform_selector(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``p`` turns the sidebar into the platform selector with the current
    selection ticked; ``j`` moves the highlight on to the next platform."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_help_screen(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """``?`` opens the help overlay listing every keybinding."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("question_mark")
        await pilot.pause()

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_file_preview_renders_info_about_json(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """Previewing ``info/about.json`` streams the file out of the real archive
    and renders it with JSON syntax highlighting."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        # Main panel -> files section -> info tab -> open the first file.
        await pilot.press("l", "tab", "tab", "]")
        await pilot.pause()
        await pilot.press("enter")
        await wait_for_idle(pilot)
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )
