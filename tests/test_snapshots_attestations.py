"""Snapshot tests of the attestation tab of the version detail view.

The signed artifact comes from the ``skill-forge`` fixture channel and its
signature is checked for real, so the screen shown here is the one a user gets
for a genuinely attested package; see ``test_snapshots.py`` for how snapshots are
reviewed.
"""

from __future__ import annotations

from textual.pilot import Pilot

from tests.helpers import (
    NARROW_TERMINAL_SIZE,
    SKILL_FORGE_CHANNEL,
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


def test_attestation_tab_shows_the_verified_signature(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """The signing identity names the workflow and ref the artifact was built
    from, and the tab label carries the ``✓`` that says so without being opened.
    """

    async def run_before(pilot: Pilot[None]) -> None:
        await open_attestation_tab(pilot, package_index=0)

    assert snap_compare_palettes(
        make_app(default_channels=[SKILL_FORGE_CHANNEL]),
        run_before=run_before,
        terminal_size=TERMINAL_SIZE,
    )


def test_attestation_tab_of_an_unsigned_artifact(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """conda-forge advertises no attestations, so the tab says as much and its
    label stays bare -- the case nearly every package is in."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_attestation_tab(pilot, package_index=1)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE
    )


def test_attestation_tab_strip_clips_in_a_narrow_terminal(
    snap_compare_palettes: SnapComparePalettes, make_app: AppFactory
) -> None:
    """A third metadata tab no longer fits beside the other two, so the strip
    clips and the active tab is kept in view."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_attestation_tab(pilot, package_index=1)

    assert snap_compare_palettes(
        make_app(), run_before=run_before, terminal_size=NARROW_TERMINAL_SIZE
    )
