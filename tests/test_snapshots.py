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

from textual.pilot import Pilot

from tests.helpers import (
    TERMINAL_SIZE,
    AppFactory,
    SnapCompare,
    open_versions,
    type_text,
    wait_for_idle,
)


def test_packages_view_lists_channel_packages(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Startup: the sidebar lists the packages and previews the first one."""
    assert snap_compare(
        make_app(), run_before=wait_for_idle, terminal_size=TERMINAL_SIZE
    )


def test_versions_view_shows_real_artifact_details(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Opening ``pixi-browse`` shows metadata, dependencies and files read
    from the real ``.conda`` archive."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_versions_view_groups_subdirs_by_latest_version(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``libzlib`` has linux-64 and osx-arm64 builds; the highlighted 1.3.2
    build shows its run exports and symlinked files."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        # Focus the main panel and switch the dependency section to run exports.
        await pilot.press("l", "tab", "]", "]")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_filter_narrows_package_list(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Typing ``six`` into the ``/`` search narrows the package list to fuzzy
    matches and previews the best one."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("slash")
        await type_text(pilot, "six")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_matchspec_query_filters_records(
    snap_compare: SnapCompare, make_app: AppFactory
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

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_matchspec_query_lists_matching_packages(
    snap_compare: SnapCompare, make_app: AppFactory
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

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_whoneeds_query_lists_dependents(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``zlib`` depends on ``libzlib``; the who-needs scan runs against the
    unsharded repodata of the fixture channel."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("w")
        await pilot.pause()
        await type_text(pilot, "libzlib")
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_platform_selector(snap_compare: SnapCompare, make_app: AppFactory) -> None:
    """``p`` turns the sidebar into the platform selector with the current
    selection ticked."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("p")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_help_screen(snap_compare: SnapCompare, make_app: AppFactory) -> None:
    """``?`` opens the help overlay listing every keybinding."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("question_mark")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_file_preview_renders_info_about_json(
    snap_compare: SnapCompare, make_app: AppFactory
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

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)
