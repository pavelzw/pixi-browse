"""Download and verify the real conda-forge artifacts of the test channel.

The artifacts listed in ``tests/fixtures/channel_artifacts.toml`` are not
committed. They are fetched into a git-ignored directory on first use and
verified by SHA256, so the test-suite is deterministic and works offline once
the files are present. Run ``pixi run fetch-test-channel`` to pre-download them.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from rattler.networking import Client
from rattler.package_streaming import download_to_path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "channel_artifacts.toml"
CHANNEL_DIR_ENV_VAR = "PIXI_BROWSE_TEST_CHANNEL_DIR"
DEFAULT_CHANNEL_DIR = FIXTURES_DIR / "channel"


@dataclass(frozen=True)
class ChannelArtifact:
    subdir: str
    file_name: str
    sha256: str

    def url(self, channel: str) -> str:
        return f"{channel}/{self.subdir}/{self.file_name}"

    def path(self, channel_dir: Path) -> Path:
        return channel_dir / self.subdir / self.file_name


@dataclass(frozen=True)
class ChannelManifest:
    channel: str
    artifacts: tuple[ChannelArtifact, ...]


def channel_dir() -> Path:
    """Where the artifacts live locally (git-ignored, overridable via env)."""
    override = os.environ.get(CHANNEL_DIR_ENV_VAR)
    return Path(override).expanduser() if override else DEFAULT_CHANNEL_DIR


def load_manifest(path: Path = MANIFEST_PATH) -> ChannelManifest:
    manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    channel = manifest["channel"]
    assert isinstance(channel, str)
    return ChannelManifest(
        channel=channel.rstrip("/"),
        artifacts=tuple(
            ChannelArtifact(
                subdir=str(entry["subdir"]),
                file_name=str(entry["file_name"]),
                sha256=str(entry["sha256"]),
            )
            for entry in manifest["artifacts"]
        ),
    )


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_valid(artifact: ChannelArtifact, path: Path) -> bool:
    return path.is_file() and _sha256_of(path) == artifact.sha256


async def _download(
    client: Client, manifest: ChannelManifest, artifact: ChannelArtifact, path: Path
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = path.with_name(path.name + ".part")
    await download_to_path(client, artifact.url(manifest.channel), partial_path)
    digest = _sha256_of(partial_path)
    if digest != artifact.sha256:
        partial_path.unlink()
        raise RuntimeError(
            f"{artifact.url(manifest.channel)}: expected sha256 {artifact.sha256}, "
            f"got {digest}"
        )
    partial_path.replace(path)


async def _ensure_artifacts_async(
    manifest: ChannelManifest, directory: Path, *, log: bool
) -> list[ChannelArtifact]:
    missing = [
        artifact
        for artifact in manifest.artifacts
        if not _is_valid(artifact, artifact.path(directory))
    ]
    if not missing:
        return []

    client = Client.default_client(user_agent="pixi-browse-tests")
    for artifact in missing:
        if log:
            print(f"fetching  {artifact.url(manifest.channel)}", file=sys.stderr)
        await _download(client, manifest, artifact, artifact.path(directory))
    return missing


def ensure_channel_artifacts(
    directory: Path | None = None, *, log: bool = False
) -> Path:
    """Make sure every manifest artifact is present and valid in ``directory``.

    Returns the directory. Downloads only what is missing or has a wrong hash.
    """
    manifest = load_manifest()
    target = directory if directory is not None else channel_dir()
    asyncio.run(_ensure_artifacts_async(manifest, target, log=log))
    return target


def main() -> int:
    manifest = load_manifest()
    directory = channel_dir()
    fetched = asyncio.run(_ensure_artifacts_async(manifest, directory, log=True))
    print(
        f"{len(manifest.artifacts)} artifacts in {directory} "
        f"({len(fetched)} downloaded, {len(manifest.artifacts) - len(fetched)} cached)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
