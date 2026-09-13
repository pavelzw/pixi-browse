"""Shared fixtures: a real, offline conda channel served over HTTP.

The channel consists of real conda-forge artifacts listed in
``tests/fixtures/channel_artifacts.toml``; ``tests/channel_artifacts.py``
downloads them into a git-ignored directory on first use and verifies their
hashes. At session start the directory is indexed with py-rattler and served by
a small HTTP server that supports range requests, the same way a real channel
mirror would. A rattler ``Config`` with local mirrors then redirects every request for
``https://conda.anaconda.org/conda-forge/`` to that server, so the app can be
started with its production defaults (``conda-forge``) and exercises the exact
gateway, repodata, package-streaming and rendering code paths it uses for
users, without network access and with deterministic data.

The manifest lists artifacts of more than one channel (``bioconda`` next to
``conda-forge``), each served under its own name, so channel switching runs
for real. ``missing`` is mirrored too but has no repodata at all, so loading
it fails.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from collections.abc import Iterable, Iterator
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from rattler.config import Config
from rattler.index import index_fs
from rattler.match_spec import MatchSpec
from rattler.networking import Client
from rattler.platform import Platform
from rattler.repo_data import Gateway
from syrupy.assertion import SnapshotAssertion

from pixi_browse.repodata import create_gateway
from pixi_browse.tui import CondaMetadataTui
from tests.channel_artifacts import ChannelManifest, ensure_channel_artifacts
from tests.helpers import (
    ANACONDA_CHANNELS_URL,
    LIGHT_PALETTE,
    MAIN_CHANNEL,
    MISSING_CHANNEL,
    TERMINAL_SIZE,
    AppFactory,
    GatewayFactory,
    LightSVGImageExtension,
    PaletteScreenshotApp,
    PilotHook,
    RangeRequestHandler,
    SnapCompare,
    report_light_comparison,
)


@pytest.fixture(scope="session")
def channel_manifest() -> ChannelManifest:
    """The manifest of test artifacts, downloaded and hash-verified."""
    return ensure_channel_artifacts()


@pytest.fixture(scope="session")
def fixture_channels_dir(
    channel_manifest: ChannelManifest, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """The manifest artifacts indexed into complete conda channels, one
    directory per channel name."""
    channels_dir = tmp_path_factory.mktemp("channels")
    for channel_name in channel_manifest.channels:
        channel_dir = channels_dir / channel_name
        for artifact in channel_manifest.artifacts_of(channel_name):
            destination = channel_dir / artifact.subdir / artifact.file_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(artifact.local_path, destination)
        asyncio.run(index_fs(channel_dir, write_zst=True, write_shards=True))
    return channels_dir


@pytest.fixture(scope="session")
def channel_server(fixture_channels_dir: Path) -> Iterator[str]:
    """Serve the indexed channels on a random loopback port."""
    handler = partial(RangeRequestHandler, directory=str(fixture_channels_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="session")
def rattler_config(channel_manifest: ChannelManifest, channel_server: str) -> Config:
    """Configure the test channels to be served from the local server
    under the URLs the app resolves their names to."""
    mirrors = {
        f"{channel_url}/": [f"{channel_server}{channel_name}/"]
        for channel_name, channel_url in channel_manifest.channels.items()
    }
    mirrors[f"{ANACONDA_CHANNELS_URL}{MISSING_CHANNEL}/"] = [
        f"{channel_server}{MISSING_CHANNEL}/"
    ]
    assert f"{ANACONDA_CHANNELS_URL}{MAIN_CHANNEL}/" in mirrors
    config = Config()
    config.set("mirrors", json.dumps(mirrors))
    return config


@pytest.fixture(scope="session")
def rattler_client(rattler_config: Config) -> Client:
    """Use the same configuration for direct package-streaming tests."""
    return Client.from_config(rattler_config, user_agent="pixi-browse-tests")


@pytest.fixture
def rattler_cache_dir(tmp_path: Path) -> Path:
    """An isolated repodata cache so tests never touch ``~/.cache/rattler``."""
    return tmp_path / "rattler-cache"


@pytest.fixture
def make_gateway(rattler_config: Config, rattler_cache_dir: Path) -> GatewayFactory:
    def factory() -> Gateway:
        return create_gateway(config=rattler_config, cache_dir=rattler_cache_dir)

    return factory


@pytest.fixture
def make_app(rattler_config: Config, rattler_cache_dir: Path) -> AppFactory:
    """Build the real app against the fixture channel."""

    def factory(
        *,
        default_channels: Iterable[str] = (MAIN_CHANNEL,),
        default_platforms: Iterable[Platform] | None = None,
        default_matchspec: MatchSpec | None = None,
    ) -> CondaMetadataTui:
        return PaletteScreenshotApp(
            default_channels=default_channels,
            default_platforms=default_platforms,
            default_matchspec=default_matchspec,
            config=rattler_config,
            cache_dir=rattler_cache_dir,
        )

    return factory


@pytest.fixture
def snap_compare(
    snap_compare: SnapCompare,
    snapshot: SnapshotAssertion,
    request: pytest.FixtureRequest,
) -> SnapCompare:
    """``pytest-textual-snapshot``'s fixture, extended by the light palette.

    The plugin's fixture compares the dark SVG of the run with the snapshot in
    ``tests/__snapshots__/<module>/``; this wrapper compares the light SVG of the
    very same run with the snapshot in ``tests/__snapshots__/light/<module>/``, so
    every screen is reviewed in both looks without running the app twice.
    """
    light_snapshot = snapshot.use_extension(LightSVGImageExtension)

    def compare(
        app: CondaMetadataTui,
        press: Iterable[str] = (),
        terminal_size: tuple[int, int] = TERMINAL_SIZE,
        run_before: PilotHook | None = None,
    ) -> bool:
        assert isinstance(app, PaletteScreenshotApp), (
            "Snapshot tests must build their app with the `make_app` fixture."
        )
        dark_matches = snap_compare(
            app, press=press, terminal_size=terminal_size, run_before=run_before
        )
        # Comparing here rather than in the test keeps syrupy from dumping the
        # line diff of two SVGs into the terminal on a mismatch.
        light_svg = app.palette_screenshots[LIGHT_PALETTE]
        light_matches = light_snapshot == light_svg
        report_light_comparison(
            request.node, light_snapshot, light_svg, matches=light_matches
        )
        if dark_matches and not light_matches:
            raise AssertionError(
                "The dark snapshot matched but the light one did not: the screen "
                "either changed in a way that only a light terminal shows, or has "
                "no light snapshot yet. Accept it with `pixi run snapshot-update`."
            )
        return dark_matches

    return compare
