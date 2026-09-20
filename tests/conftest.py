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
from contextlib import contextmanager
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
from textual._doc import take_svg_screenshot

from pixi_browse.repodata import create_gateway
from pixi_browse.tui import CondaMetadataTui
from tests.channel_artifacts import ChannelManifest, ensure_channel_artifacts
from tests.helpers import (
    ANACONDA_CHANNELS_URL,
    BIOCONDA_CHANNEL,
    MAIN_CHANNEL,
    MISSING_CHANNEL,
    TERMINAL_SIZE,
    AppFactory,
    GatewayFactory,
    HeldSubdirRequestHandler,
    PaletteScreenshotApp,
    PaletteSVGImageExtension,
    PilotHook,
    RangeRequestHandler,
    SnapComparePalettes,
    SubdirHold,
    report_palette_comparison,
    still_cursors_after,
)


@pytest.fixture(scope="session")
def channel_manifest() -> ChannelManifest:
    """The manifest of test artifacts, downloaded and hash-verified."""
    return ensure_channel_artifacts()


# A publication (CEP-0047) is assigned by the indexer, so it lands one day
# after the artifact was built rather than at a time the artifact itself knows.
INDEXED_TIMESTAMP_DELAY_MS = 24 * 60 * 60 * 1000
# Artifacts too old to carry a build timestamp still need a publication, so they
# share this one: 2026-01-01T00:00:00Z in Unix milliseconds.
INDEXED_TIMESTAMP_FALLBACK_MS = 1767225600000


def _freeze_indexed_timestamps(channel_dir: Path) -> None:
    """Rewrite the ``indexed_timestamp`` of every indexed record to a value
    derived from the artifact's build timestamp.

    ``index_fs`` stamps records it publishes for the first time with the current
    time, which would change every snapshot on every run. Reindexing preserves
    the publications already in ``repodata.json``, so rewriting them there and
    indexing once more spreads the frozen values to the zstd and sharded
    repodata the app actually reads.
    """
    for repodata_path in sorted(channel_dir.glob("*/repodata.json")):
        repodata = json.loads(repodata_path.read_text())
        for key in ("packages", "packages.conda"):
            for record in repodata.get(key, {}).values():
                build_timestamp = record.get("timestamp")
                record["indexed_timestamp"] = (
                    build_timestamp + INDEXED_TIMESTAMP_DELAY_MS
                    if build_timestamp is not None
                    else INDEXED_TIMESTAMP_FALLBACK_MS
                )
        repodata_path.write_text(json.dumps(repodata))


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
        _freeze_indexed_timestamps(channel_dir)
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


def mirrored_config(channel_manifest: ChannelManifest, channel_server: str) -> Config:
    """Configure the test channels to be served from ``channel_server`` under
    the URLs the app resolves their names to."""
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
def rattler_config(channel_manifest: ChannelManifest, channel_server: str) -> Config:
    return mirrored_config(channel_manifest, channel_server)


@contextmanager
def held_subdir_config(
    channel_manifest: ChannelManifest,
    channels_dir: Path,
    channel_name: str,
    subdir: str,
) -> Iterator[Config]:
    """A configuration whose mirror of ``channel_name`` never answers for
    ``subdir`` while the block runs.

    Every other subdir is served as usual, so the app gets as far as the
    repodata loading screen with all probes but one finished and stays there:
    the state a slow, unsharded channel leaves a user in for minutes. The held
    requests are released on exit.
    """
    hold = SubdirHold(channel_name, subdir)
    handler = partial(HeldSubdirRequestHandler, directory=str(channels_dir), hold=hold)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield mirrored_config(
            channel_manifest, f"http://127.0.0.1:{server.server_port}/"
        )
    finally:
        hold.release.set()
        server.shutdown()
        server.server_close()


@pytest.fixture
def stalled_linux64_config(
    channel_manifest: ChannelManifest, fixture_channels_dir: Path
) -> Iterator[Config]:
    """``conda-forge`` with its ``linux-64`` subdir never answering, so the
    startup load stays on the loading screen."""
    with held_subdir_config(
        channel_manifest, fixture_channels_dir, MAIN_CHANNEL, "linux-64"
    ) as config:
        yield config


@pytest.fixture
def stalled_bioconda_config(
    channel_manifest: ChannelManifest, fixture_channels_dir: Path
) -> Iterator[Config]:
    """``bioconda`` with its only subdir, ``noarch``, never answering, so a
    switch to it stays on the loading screen while ``conda-forge`` loads."""
    with held_subdir_config(
        channel_manifest, fixture_channels_dir, BIOCONDA_CHANNEL, "noarch"
    ) as config:
        yield config


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
        config: Config | None = None,
    ) -> CondaMetadataTui:
        return PaletteScreenshotApp(
            default_channels=default_channels,
            default_platforms=default_platforms,
            default_matchspec=default_matchspec,
            config=config if config is not None else rattler_config,
            cache_dir=rattler_cache_dir,
        )

    return factory


@pytest.fixture
def snap_compare_palettes(
    snapshot: SnapshotAssertion, request: pytest.FixtureRequest
) -> SnapComparePalettes:
    """Compare a screen with its snapshot in every palette.

    This is used instead of ``pytest-textual-snapshot``'s ``snap_compare``,
    which compares a single screenshot with a single snapshot. The app is run
    and screenshotted exactly the way the plugin does it, but every palette of
    the run (see ``tests.helpers.SVG_PALETTES``) is compared with a snapshot of
    its own in ``tests/__snapshots__/<module>/<test>.<palette>.svg``, and every
    comparison lands in ``snapshot_report.html`` as the plugin's own do.
    """
    snapshot = snapshot.use_extension(PaletteSVGImageExtension)

    def compare(
        app: CondaMetadataTui,
        press: Iterable[str] = (),
        terminal_size: tuple[int, int] = TERMINAL_SIZE,
        run_before: PilotHook | None = None,
    ) -> bool:
        assert isinstance(app, PaletteScreenshotApp), (
            "Snapshot tests must build their app with the `make_app` fixture."
        )
        # Runs the app and screenshots it, which fills `palette_screenshots`.
        take_svg_screenshot(
            app=app,
            press=press,
            terminal_size=terminal_size,
            run_before=partial(still_cursors_after, run_before),
        )
        unmatched: list[str] = []
        for palette, svg in app.palette_screenshots.items():
            # Comparing here rather than in the test keeps syrupy from dumping
            # the line diff of two SVGs into the terminal on a mismatch.
            matches = snapshot(name=palette) == svg
            report_palette_comparison(
                request.node, snapshot, palette, svg, matches=matches
            )
            if not matches:
                unmatched.append(palette)
        if unmatched and len(unmatched) < len(app.palette_screenshots):
            raise AssertionError(
                f"Only some palettes did not match: {', '.join(unmatched)}. The "
                "screen either changed in a way that only those palettes show, or "
                "has no snapshot for them yet. Accept them with "
                "`pixi run snapshot-update`."
            )
        return not unmatched

    return compare
