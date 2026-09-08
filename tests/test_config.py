"""Configuration integration tests using real channel data and HTTP requests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import override

import pytest
from rattler.config import Config
from rattler.package_streaming import download_to_path, fetch_raw_package_file_from_url
from rattler.platform import Platform
from syrupy.assertion import SnapshotAssertion

from pixi_browse.__main__ import build_app
from pixi_browse.repodata import create_gateway, query_package_records
from tests.helpers import RangeRequestHandler


@pytest.fixture
def recording_channel_server(
    fixture_channels_dir: Path,
) -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Handler(RangeRequestHandler):
        @override
        def do_GET(self) -> None:
            requests.append(self.path.split("?", 1)[0])
            super().do_GET()

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Handler, directory=str(fixture_channels_dir))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("per_channel", [False, True])
@pytest.mark.parametrize("transport", ["mirror", "s3"])
def test_config_repodata_previews_and_downloads(
    tmp_path: Path,
    recording_channel_server: tuple[str, list[str]],
    snapshot: SnapshotAssertion,
    per_channel: bool,
    transport: str,
) -> None:
    server_url, requests = recording_channel_server
    if transport == "s3":
        upstream = "s3://conda-forge"
        credentials = tmp_path / "credentials.json"
        credentials.write_text(
            json.dumps(
                {
                    upstream: {
                        "S3Credentials": {
                            "access_key_id": "test-access-key",
                            "secret_access_key": "test-secret-key",
                        }
                    }
                }
            )
        )
        network_config = f'''
authentication-override-file = {json.dumps(str(credentials))}
[s3-options.conda-forge]
endpoint-url = "{server_url}"
region = "us-east-1"
force-path-style = true
'''
    else:
        upstream = "https://config.invalid/conda-forge"
        network_config = f'''
[mirrors]
"{upstream}" = ["{server_url}/conda-forge"]
'''
    scope = f'repodata-config."{upstream}"' if per_channel else "repodata-config"
    path = tmp_path / "config.toml"
    path.write_text(f'''
default-channels = ["{upstream}"]
{network_config}
[{scope}]
disable-sharded = true
disable-zstd = true
disable-bzip2 = true
''')
    app = build_app(
        channels=None,
        platforms=["noarch"],
        matchspec=None,
        config_path=path,
        cache_dir=tmp_path / "cache",
    )

    async def run() -> dict[str, object]:
        records = await query_package_records(
            gateway=app._gateway,
            channel_names=app._channel_names,
            platforms=[Platform("noarch")],
            package_name="pixi-browse",
        )
        record = records[0]
        preview = await fetch_raw_package_file_from_url(
            app._client, record.url, "info/about.json"
        )
        destination = tmp_path / record.file_name
        await download_to_path(app._client, record.url, destination)
        assert hashlib.sha256(destination.read_bytes()).digest() == record.sha256
        return {
            "channels": app._channel_names,
            "packages": [record.file_name for record in records],
            "about": json.loads(preview),
            "requests": sorted(set(requests)),
        }

    assert asyncio.run(run()) == snapshot
    assert "/conda-forge/noarch/repodata.json" in requests
    assert not any(
        "shard" in path or path.endswith((".zst", ".bz2")) for path in requests
    )


def test_whoneeds_disables_shards_even_with_channel_override(
    tmp_path: Path,
    recording_channel_server: tuple[str, list[str]],
    snapshot: SnapshotAssertion,
) -> None:
    server_url, requests = recording_channel_server
    upstream = "https://config.invalid/conda-forge"
    config = Config.from_toml(f'''
[mirrors]
"{upstream}" = ["{server_url}/conda-forge"]
[repodata-config]
disable-sharded = false
disable-zstd = true
disable-bzip2 = true
[repodata-config."{upstream}"]
disable-sharded = false
''')
    original = config.to_toml()
    gateway = create_gateway(
        config=config, sharded_enabled=False, cache_dir=tmp_path / "cache"
    )
    assert config.to_toml() == original

    async def run() -> list[str]:
        results = await gateway.who_needs(
            sources=[upstream], platforms=[Platform("linux-64")], target="libzlib"
        )
        return sorted({result.record.file_name for result in results})

    assert asyncio.run(run()) == snapshot
    assert "/conda-forge/linux-64/repodata.json" in requests
    assert not any("shard" in path for path in requests)


def test_default_locations_read_pixi_home_and_explicit_config_replaces_it(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    # A child process gives rattler a real, isolated PIXI_HOME without
    # changing the environment of other tests or patching config discovery.
    pixi_home = tmp_path / "pixi"
    pixi_home.mkdir()
    (pixi_home / "config.toml").write_text(
        'default-channels = ["bioconda", "conda-forge"]'
    )
    explicit = tmp_path / "explicit.toml"
    explicit.write_text('default-channels = ["https://prefix.dev/conda-forge"]')
    script = """
import json
import sys
from pathlib import Path
from pixi_browse.__main__ import build_app

default = build_app(channels=None, platforms=None, matchspec=None)
explicit = build_app(channels=None, platforms=None, matchspec=None, config_path=Path(sys.argv[1]))
print(json.dumps([default._channel_names, explicit._channel_names]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(explicit)],
        env={**os.environ, "PIXI_HOME": str(pixi_home)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == snapshot
