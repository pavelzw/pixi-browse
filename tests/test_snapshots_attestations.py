"""Snapshot tests of the attestation tab of the version detail view.

The ``skill-forge`` fixture deliberately pairs adjacent package and sidecar
versions, so the screen shows the real verification rejection a mismatched
subject produces; see ``test_snapshots.py`` for how snapshots are reviewed.
"""

from __future__ import annotations

from textual.pilot import Pilot

from tests.helpers import (
    NARROW_TERMINAL_SIZE,
    SIGNING_TESTS_CHANNEL,
    TERMINAL_SIZE,
    AppFactory,
    SnapComparePalettes,
    open_versions,
    wait_for_idle,
)


async def open_attestation_tab(pilot: Pilot[None], package_index: int) -> None:
    """Highlight an artifact and switch its metadata section past the repodata
    patches tab to the attestation one."""
    await open_versions(pilot, package_index=package_index)
    await pilot.press("l", "]", "]")
    await wait_for_idle(pilot)


def test_attestation_tab_shows_the_rejected_sidecar(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """The committed sidecar belongs to the next package version, so its subject
    mismatch is visible and the tab label carries ``✗`` without being opened.
    """

    async def run_before(pilot: Pilot[None]) -> None:
        await open_attestation_tab(pilot, package_index=0)

    assert snap_compare_palettes(
        make_app(default_channels=[SIGNING_TESTS_CHANNEL]),
        run_before=run_before,
        terminal_size=TERMINAL_SIZE,
    )


def test_attestation_tab_of_an_unsigned_artifact(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """conda-forge advertises no attestations, so the tab says as much and its
    label stays bare -- the case nearly every package is in."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_attestation_tab(pilot, package_index=2)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_attestation_tab_strip_clips_in_a_narrow_terminal(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """A third metadata tab no longer fits beside the other two, so the strip
    clips and the active tab is kept in view."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_attestation_tab(pilot, package_index=2)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=NARROW_TERMINAL_SIZE
    )
