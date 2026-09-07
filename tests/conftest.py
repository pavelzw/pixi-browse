"""Shared fixtures: a real, offline conda channel served over HTTP.

The channel in ``tests/fixtures/channel`` consists of real conda-forge
artifacts (see ``artifacts.toml`` there). At session start it is indexed with
py-rattler and served by a small HTTP server that supports range requests, the
same way a real channel mirror would. A rattler ``Client`` with a
``MirrorMiddleware`` then transparently redirects every request for
``https://conda.anaconda.org/conda-forge/`` to that server, so the app can be
started with its production defaults (``conda-forge``) and exercises the exact
gateway, repodata, package-streaming and rendering code paths it uses for
users, without network access and with deterministic data.
"""

from __future__ import annotations

import asyncio
import shutil
import threading
from collections.abc import Iterable, Iterator
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from rattler.index import index_fs
from rattler.match_spec import MatchSpec
from rattler.networking import Client
from rattler.networking.middleware import MirrorMiddleware
from rattler.platform import Platform
from rattler.repo_data import Gateway

from pixi_browse.repodata import create_gateway
from pixi_browse.tui import CondaMetadataTui
from tests.helpers import (
    CHANNEL_SOURCE_DIR,
    UPSTREAM_CHANNEL_URL,
    AppFactory,
    GatewayFactory,
    RangeRequestHandler,
)


@pytest.fixture(scope="session")
def fixture_channel_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of the fixture artifacts, indexed into a complete conda channel."""
    channel_dir = tmp_path_factory.mktemp("channel")
    for subdir in sorted(CHANNEL_SOURCE_DIR.iterdir()):
        if subdir.is_dir():
            shutil.copytree(subdir, channel_dir / subdir.name)
    asyncio.run(index_fs(channel_dir, write_zst=True, write_shards=True))
    return channel_dir


@pytest.fixture(scope="session")
def channel_server(fixture_channel_dir: Path) -> Iterator[str]:
    """Serve the indexed channel on a random loopback port."""
    handler = partial(RangeRequestHandler, directory=str(fixture_channel_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="session")
def rattler_client(channel_server: str) -> Client:
    """A rattler client that serves ``conda-forge`` from the fixture channel."""
    return Client(
        middlewares=[MirrorMiddleware({UPSTREAM_CHANNEL_URL: [channel_server]})],
        user_agent="pixi-browse-tests",
    )


@pytest.fixture
def rattler_cache_dir(tmp_path: Path) -> Path:
    """An isolated repodata cache so tests never touch ``~/.cache/rattler``."""
    return tmp_path / "rattler-cache"


@pytest.fixture
def make_gateway(rattler_client: Client, rattler_cache_dir: Path) -> GatewayFactory:
    def factory(*, sharded_enabled: bool = True) -> Gateway:
        return create_gateway(
            client=rattler_client,
            sharded_enabled=sharded_enabled,
            cache_dir=rattler_cache_dir,
        )

    return factory


@pytest.fixture
def make_app(rattler_client: Client, rattler_cache_dir: Path) -> AppFactory:
    """Build the real app against the fixture channel."""

    def factory(
        *,
        default_channel: str = "conda-forge",
        default_platforms: Iterable[Platform] | None = None,
        default_matchspec: MatchSpec | None = None,
    ) -> CondaMetadataTui:
        return CondaMetadataTui(
            default_channel=default_channel,
            default_platforms=default_platforms,
            default_matchspec=default_matchspec,
            client=rattler_client,
            cache_dir=rattler_cache_dir,
        )

    return factory
