"""Download and verify the real artifacts of the offline test channels.

The artifacts listed in ``tests/fixtures/channel_artifacts.toml`` are not
committed. They are fetched into the git-ignored ``tests/fixtures/channels``
directory (one subdirectory per channel) on first use and verified by SHA256,
so the test-suite is deterministic and works offline once the files are
present. Run ``pixi run fetch-test-channel`` to pre-download them.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock
from rattler.networking import Client
from rattler.package_streaming import download_to_path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "channel_artifacts.toml"
# Git-ignored download location, one subdirectory per channel name.
CHANNELS_DIR = FIXTURES_DIR / "channels"
# Serializes the download across processes; inside the git-ignored directory.
LOCK_PATH = CHANNELS_DIR / ".lock"


@dataclass(frozen=True)
class ChannelArtifact:
    channel: str
    subdir: str
    file_name: str
    sha256: str

    def url(self, channel_url: str) -> str:
        return f"{channel_url}/{self.subdir}/{self.file_name}"

    @property
    def local_path(self) -> Path:
        return CHANNELS_DIR / self.channel / self.subdir / self.file_name


@dataclass(frozen=True)
class ChannelManifest:
    #: Channel name (as typed into the app) to the URL the artifacts come from.
    channels: dict[str, str]
    artifacts: tuple[ChannelArtifact, ...]

    def url(self, artifact: ChannelArtifact) -> str:
        return artifact.url(self.channels[artifact.channel])

    def artifacts_of(self, channel: str) -> tuple[ChannelArtifact, ...]:
        return tuple(
            artifact for artifact in self.artifacts if artifact.channel == channel
        )


def load_manifest(path: Path = MANIFEST_PATH) -> ChannelManifest:
    manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    channels = {
        str(name): str(url).rstrip("/") for name, url in manifest["channels"].items()
    }
    artifacts = tuple(
        ChannelArtifact(
            channel=str(entry["channel"]),
            subdir=str(entry["subdir"]),
            file_name=str(entry["file_name"]),
            sha256=str(entry["sha256"]),
        )
        for entry in manifest["artifacts"]
    )
    unknown = sorted({a.channel for a in artifacts} - channels.keys())
    if unknown:
        raise ValueError(f"artifacts reference undefined channels: {unknown}")
    return ChannelManifest(channels=channels, artifacts=artifacts)


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_valid(artifact: ChannelArtifact) -> bool:
    path = artifact.local_path
    return path.is_file() and _sha256_of(path) == artifact.sha256


async def _download(
    client: Client, manifest: ChannelManifest, artifact: ChannelArtifact
) -> None:
    path = artifact.local_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # The partial file is process-specific so that parallel test workers
    # (``pytest -n auto``) downloading the same artifact cannot delete or
    # rename each other's in-progress download. The final rename is atomic and
    # every writer produces the same, hash-verified bytes.
    partial_path = path.with_name(f"{path.name}.{os.getpid()}.part")
    url = manifest.url(artifact)
    try:
        await download_to_path(client, url, partial_path)
        digest = _sha256_of(partial_path)
        if digest != artifact.sha256:
            raise RuntimeError(
                f"{url}: expected sha256 {artifact.sha256}, got {digest}"
            )
        partial_path.replace(path)
    finally:
        partial_path.unlink(missing_ok=True)


async def _download_missing(manifest: ChannelManifest) -> int:
    missing = [artifact for artifact in manifest.artifacts if not _is_valid(artifact)]
    if not missing:
        return 0

    client = Client.default_client(user_agent="pixi-browse-tests")
    for artifact in missing:
        print(f"fetching  {manifest.url(artifact)}", file=sys.stderr)
        await _download(client, manifest, artifact)
    return len(missing)


def _download_missing_exclusively(manifest: ChannelManifest) -> int:
    """Run :func:`_download_missing` as the only process doing so.

    Under ``pytest -n auto`` every worker is its own process and runs this, so
    without the lock they all produce the same artifacts at the same time and
    then rename their own copy onto the same destination. POSIX lets both
    renames through, but on Windows renaming onto a path that another process
    holds open is denied, and the workers hold each other's destinations open
    both to hash them and to copy them into their channel directories. The
    loser's rename then fails with ``PermissionError``, which takes its
    session-scoped fixture and therefore every test in that worker with it.

    Holding the lock across the whole scan-and-download makes the first
    process in do the work while the others wait, and they then find every
    artifact already valid and download nothing.
    """
    CHANNELS_DIR.mkdir(parents=True, exist_ok=True)
    with FileLock(LOCK_PATH):
        return asyncio.run(_download_missing(manifest))


def ensure_channel_artifacts() -> ChannelManifest:
    """Download every manifest artifact that is missing or has a wrong hash."""
    manifest = load_manifest()
    _download_missing_exclusively(manifest)
    return manifest


def main() -> int:
    manifest = load_manifest()
    fetched = _download_missing_exclusively(manifest)
    print(
        f"{len(manifest.artifacts)} artifacts of {len(manifest.channels)} channels "
        f"in {CHANNELS_DIR} ({fetched} downloaded, "
        f"{len(manifest.artifacts) - fetched} cached)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
