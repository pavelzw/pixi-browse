"""Downloads from the real archives of the offline channel.

These are not snapshot tests because the notifications and the download
prompt show the absolute destination path, which differs per machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from pathlib import Path

import pytest
from rattler.networking import Client
from rattler.package_streaming import fetch_raw_package_file_from_url

from pixi_browse.tui import CondaMetadataTui, FileActionScreen
from tests.channel_artifacts import load_manifest
from tests.helpers import (
    TERMINAL_SIZE,
    UPSTREAM_CHANNEL_URL,
    AppFactory,
    notification_messages,
    open_versions,
    type_text,
    wait_for_idle,
    wait_for_screen,
)

PIXI_BROWSE_FILE_NAME = "pixi-browse-0.0.14-pyhc364b38_0.conda"


def test_d_downloads_highlighted_artifact_to_cwd(
    make_app: AppFactory, tmp_path: Path
) -> None:
    expected_sha256 = next(
        artifact.sha256
        for artifact in load_manifest().artifacts
        if artifact.file_name == PIXI_BROWSE_FILE_NAME
    )

    async def run() -> None:
        app = make_app()
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


def test_file_action_downloads_file_to_chosen_destination(
    make_app: AppFactory, rattler_client: Client, tmp_path: Path
) -> None:
    expected = asyncio.run(
        fetch_raw_package_file_from_url(
            rattler_client,
            f"{UPSTREAM_CHANNEL_URL}noarch/{PIXI_BROWSE_FILE_NAME}",
            "info/about.json",
        )
    )

    async def run() -> None:
        app = make_app()
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
                f"Files: Downloaded file to {tmp_path / 'downloads' / 'about.json'}"
            ]

    with contextlib.chdir(tmp_path):
        asyncio.run(run())

    assert (tmp_path / "downloads" / "about.json").read_bytes() == expected


def test_file_destination_path_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)

    with (
        contextlib.chdir(tmp_path),
        pytest.raises(RuntimeError, match="Unsafe package file path"),
    ):
        CondaMetadataTui._file_destination_path("link/demo.py")


def test_compare_file_action_downloads_right_side_file(
    make_app: AppFactory, rattler_client: Client, tmp_path: Path
) -> None:
    """``Download right`` on a compare row fetches the file from the right
    build's archive (``libzlib 1.3.2`` here)."""
    expected = asyncio.run(
        fetch_raw_package_file_from_url(
            rattler_client,
            f"{UPSTREAM_CHANNEL_URL}linux-64/libzlib-1.3.2-h25fd6f3_3.conda",
            "lib/libz.so.1.3.2",
        )
    )

    async def run() -> None:
        app = make_app()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            # Compare libzlib 1.3.2 (compare A) with 1.3.1 on linux-64.
            await open_versions(pilot, package_index=0)
            await pilot.press("C", "j")
            await wait_for_idle(pilot)
            await pilot.press("C")
            await wait_for_idle(pilot)
            # Row 2 is lib/libz.so.1.3.2, which only the right build has, so
            # its actions are "Preview right" and "Download right".
            await pilot.press("3", "j", "j", "enter")
            await wait_for_screen(pilot, FileActionScreen)
            await pilot.press("down", "enter")
            await pilot.pause()
            await type_text(pilot, "right/libz.so")
            await pilot.press("enter")
            await wait_for_idle(pilot)
            assert notification_messages(app)[-1] == (
                f"Files: Downloaded file to {tmp_path / 'right' / 'libz.so'}"
            )

    with contextlib.chdir(tmp_path):
        asyncio.run(run())

    assert (tmp_path / "right" / "libz.so").read_bytes() == expected


@pytest.mark.parametrize("file_path", ["/etc/passwd", "../outside.py"])
def test_file_destination_path_rejects_absolute_and_parent_paths(
    file_path: str,
) -> None:
    with pytest.raises(RuntimeError, match="Unsafe package file path"):
        CondaMetadataTui._file_destination_path(file_path)
