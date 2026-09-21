"""Browsing a channel that is a directory on this machine.

``pixi-browse -c ./skill-forge`` resolves the channel to ``file://`` record
URLs. The repodata of such a channel is read straight off the disk, but the
archives were not: the HTTP client cannot request a ``file://`` URL, so every
package read failed and the preview never left "Loading package
information...". These tests browse the fixture channels through their
directory instead of through the HTTP server the other tests use.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import shutil
from pathlib import Path

from rattler.networking import Client
from textual.widgets import Static

from pixi_browse.archives import local_archive_path, read_package_archive_file
from pixi_browse.tui import FileActionScreen
from tests.channel_artifacts import load_manifest
from tests.helpers import (
    MAIN_CHANNEL,
    TERMINAL_SIZE,
    AppFactory,
    notification_messages,
    open_versions,
    type_text,
    wait_for_idle,
    wait_for_screen,
)

PIXI_BROWSE_FILE_NAME = "pixi-browse-0.0.15-pyhc364b38_0.conda"


def test_local_archive_path_only_resolves_local_file_urls() -> None:
    assert local_archive_path(
        "file:///channels/conda-forge/noarch/pixi-browse-0.0.15-pyhc364b38_0.conda"
    ) == Path("/channels/conda-forge/noarch/pixi-browse-0.0.15-pyhc364b38_0.conda")
    # A percent-encoded path is the only way a channel directory with a space
    # reaches us, and `localhost` means this machine just as an empty host does.
    assert local_archive_path(
        "file://localhost/channels/skill%20forge/a.conda"
    ) == Path("/channels/skill forge/a.conda")
    assert (
        local_archive_path("https://conda.anaconda.org/conda-forge/noarch/a.conda")
        is None
    )
    # Another host's share is not this machine's path.
    assert local_archive_path("file://fileserver/channels/a.conda") is None


def test_version_details_load_from_a_directory_channel(
    make_app: AppFactory, fixture_channels_dir: Path
) -> None:
    """The archive of a directory channel is read from its path, so the preview
    shows the details instead of hanging on the loading placeholder."""

    async def run() -> None:
        app = make_app(default_channels=(str(fixture_channels_dir / MAIN_CHANNEL),))
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_versions(pilot, package_index=1)

            preview_key = app._previewed_version_key
            assert preview_key is not None, (
                "the highlighted artifact rendered no details"
            )
            details = app._version_artifact_data_cache[preview_key]
            assert details.package_url.startswith("file://")
            # Only the archive itself knows these, so reading it worked.
            assert details.file_paths
            assert [file.path for file in details.info_files if file.path]
            assert details.repository_urls

    asyncio.run(run())


def test_download_from_a_directory_channel(
    make_app: AppFactory, fixture_channels_dir: Path, tmp_path: Path
) -> None:
    """An artifact of a directory channel is copied rather than downloaded."""
    expected_sha256 = next(
        artifact.sha256
        for artifact in load_manifest().artifacts
        if artifact.file_name == PIXI_BROWSE_FILE_NAME
    )

    async def run() -> None:
        app = make_app(default_channels=(str(fixture_channels_dir / MAIN_CHANNEL),))
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_versions(pilot, package_index=1)
            await pilot.press("d")
            await wait_for_idle(pilot)
            assert notification_messages(app) == [
                f"Download: Downloaded successfully to {tmp_path / PIXI_BROWSE_FILE_NAME}"
            ]

    with contextlib.chdir(tmp_path):
        asyncio.run(run())

    downloaded = tmp_path / PIXI_BROWSE_FILE_NAME
    assert hashlib.sha256(downloaded.read_bytes()).hexdigest() == expected_sha256
    assert not (tmp_path / f"{PIXI_BROWSE_FILE_NAME}.part").exists()


def test_file_download_from_a_directory_channel(
    make_app: AppFactory, fixture_channels_dir: Path, tmp_path: Path
) -> None:
    """A single file out of an archive of a directory channel is read from the
    archive on disk, without the range requests a URL would need."""
    destination = tmp_path / "downloads" / "about.json"

    async def run() -> None:
        app = make_app(default_channels=(str(fixture_channels_dir / MAIN_CHANNEL),))
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_versions(pilot, package_index=1)
            # info/ tab, first file is about.json; "Download as file".
            await pilot.press("3", "]", "enter")
            await wait_for_screen(pilot, FileActionScreen)
            await pilot.press("down", "enter")
            await pilot.pause()
            # The default destination is selected, so typing replaces it.
            await type_text(pilot, "downloads/about.json")
            await pilot.press("enter")
            await wait_for_idle(pilot)
            assert notification_messages(app) == [
                f"Files: Downloaded file to {destination}"
            ]

    with contextlib.chdir(tmp_path):
        asyncio.run(run())

    archive_path = (
        fixture_channels_dir / MAIN_CHANNEL / "noarch" / PIXI_BROWSE_FILE_NAME
    )
    expected = asyncio.run(
        read_package_archive_file(Client(), archive_path.as_uri(), "info/about.json")
    )
    assert destination.read_bytes() == expected


def test_unreadable_artifact_reports_instead_of_loading_forever(
    make_app: AppFactory, fixture_channels_dir: Path, tmp_path: Path
) -> None:
    """A listed artifact that cannot be read leaves the preview with the reason.

    The repodata of the channel copied here lists every artifact, but the one
    the test highlights is gone from the directory. Reading it has to fail, and
    a swallowed failure would leave "Loading package information..." on screen
    for as long as the user keeps the entry highlighted.
    """
    channel_dir = tmp_path / "incomplete-channel"
    shutil.copytree(fixture_channels_dir / MAIN_CHANNEL, channel_dir)
    (channel_dir / "noarch" / PIXI_BROWSE_FILE_NAME).unlink()

    async def run() -> None:
        app = make_app(default_channels=(str(channel_dir),))
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await open_versions(pilot, package_index=1)

            assert app._previewed_version_key is None
            placeholder = str(app.query_one("#main-placeholder", Static).content)
            assert "Loading package information" not in placeholder
            assert "Failed to load package information" in placeholder

    asyncio.run(run())
