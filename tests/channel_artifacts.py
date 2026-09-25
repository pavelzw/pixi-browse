"""Download and verify the real artifacts of the offline test channels.

The artifacts listed in ``tests/fixtures/channel_artifacts.toml`` are not
committed. They are fetched into the git-ignored ``tests/fixtures/channels``
directory (one subdirectory per channel) on first use and verified by SHA256,
so the test-suite is deterministic and works offline once the files are
present. Run ``pixi run fetch-test-channel`` to pre-download them.

An artifact can pin the digest of a Sigstore attestation sidecar as well, which
is fetched and verified beside the archive; :mod:`tests.conftest` then publishes
it under both file names CEP 50 gives it before indexing.
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
# Committed CEP-6 ``notices.json`` files, one per channel name that has notices.
CHANNEL_NOTICES_DIR = FIXTURES_DIR / "channel_notices"


@dataclass(frozen=True)
class ChannelArtifact:
    channel: str
    subdir: str
    file_name: str
    sha256: str
    #: The digest of the Sigstore attestation sidecar the channel publishes for
    #: this artifact, where it publishes one at all. CEP 50 makes it the name of
    #: the immutable sidecar as well, so it is all that is needed to fetch it.
    attestations_sha256: str | None = None

    def url(self, channel_url: str) -> str:
        return f"{channel_url}/{self.subdir}/{self.file_name}"

    @property
    def attestations_file_name(self) -> str | None:
        if self.attestations_sha256 is None:
            return None
        return f"{self.file_name}.sigs.{self.attestations_sha256}"

    @property
    def local_path(self) -> Path:
        return CHANNELS_DIR / self.channel / self.subdir / self.file_name

    @property
    def attestations_local_path(self) -> Path | None:
        attestations_file_name = self.attestations_file_name
        if attestations_file_name is None:
            return None
        return self.local_path.with_name(attestations_file_name)


@dataclass(frozen=True)
class RemoteFile:
    """One file of the fixture channels: where it comes from, where it belongs
    and what it has to hash to."""

    url: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class ChannelSource:
    """A test channel: the URL it is known by, and the one it is fetched from.

    The two are the same for a channel that serves its own artifacts. Only the
    download side may be swapped for a preview deployment or a mirror: the
    records of a channel carry the URL it is known by, and an attestation's
    ``targetChannel`` is bound to exactly that.
    """

    url: str
    download_url: str


@dataclass(frozen=True)
class ChannelManifest:
    #: Channel name (as typed into the app) to where that channel is.
    channels: dict[str, ChannelSource]
    artifacts: tuple[ChannelArtifact, ...]

    def url(self, artifact: ChannelArtifact) -> str:
        return artifact.url(self.channels[artifact.channel].download_url)

    def artifacts_of(self, channel: str) -> tuple[ChannelArtifact, ...]:
        return tuple(
            artifact for artifact in self.artifacts if artifact.channel == channel
        )

    def remote_files(self) -> tuple[RemoteFile, ...]:
        """Every file to download: the archives and their attestation
        sidecars."""
        files: list[RemoteFile] = []
        for artifact in self.artifacts:
            url = self.url(artifact)
            files.append(
                RemoteFile(url=url, path=artifact.local_path, sha256=artifact.sha256)
            )
            sidecar_path = artifact.attestations_local_path
            if sidecar_path is None or artifact.attestations_sha256 is None:
                continue
            files.append(
                RemoteFile(
                    url=f"{url.rsplit('/', 1)[0]}/{artifact.attestations_file_name}",
                    path=sidecar_path,
                    sha256=artifact.attestations_sha256,
                )
            )
        return tuple(files)


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _channel_source(entry: object) -> ChannelSource:
    """A ``[channels]`` value: a URL, or a table that overrides where to
    download from."""
    if isinstance(entry, str):
        url = entry.rstrip("/")
        return ChannelSource(url=url, download_url=url)
    if not isinstance(entry, dict):
        raise ValueError(f"a channel is a URL or a table, not {entry!r}")
    url = str(entry["url"]).rstrip("/")
    return ChannelSource(
        url=url, download_url=str(entry.get("download_url", url)).rstrip("/")
    )


def load_manifest(path: Path = MANIFEST_PATH) -> ChannelManifest:
    manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    channels = {
        str(name): _channel_source(entry)
        for name, entry in manifest["channels"].items()
    }
    artifacts = tuple(
        ChannelArtifact(
            channel=str(entry["channel"]),
            subdir=str(entry["subdir"]),
            file_name=str(entry["file_name"]),
            sha256=str(entry["sha256"]),
            attestations_sha256=_optional_str(entry.get("attestations_sha256")),
        )
        for entry in manifest["artifacts"]
    )
    unknown = sorted({a.channel for a in artifacts} - channels.keys())
    if unknown:
        raise ValueError(f"artifacts reference undefined channels: {unknown}")
    return ChannelManifest(channels=channels, artifacts=artifacts)


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_valid(remote: RemoteFile) -> bool:
    return remote.path.is_file() and _sha256_of(remote.path) == remote.sha256


async def _download(client: Client, remote: RemoteFile) -> None:
    remote.path.parent.mkdir(parents=True, exist_ok=True)
    # The partial file is process-specific so that parallel test workers
    # (``pytest -n auto``) downloading the same artifact cannot delete or
    # rename each other's in-progress download. The final rename is atomic and
    # every writer produces the same, hash-verified bytes.
    partial_path = remote.path.with_name(f"{remote.path.name}.{os.getpid()}.part")
    try:
        await download_to_path(client, remote.url, partial_path)
        digest = _sha256_of(partial_path)
        if digest != remote.sha256:
            raise RuntimeError(
                f"{remote.url}: expected sha256 {remote.sha256}, got {digest}"
            )
        partial_path.replace(remote.path)
    finally:
        partial_path.unlink(missing_ok=True)


async def _download_missing(manifest: ChannelManifest) -> int:
    missing = [remote for remote in manifest.remote_files() if not _is_valid(remote)]
    if not missing:
        return 0

    client = Client.default_client(user_agent="pixi-browse-tests")
    for remote in missing:
        print(f"fetching  {remote.url}", file=sys.stderr)
        await _download(client, remote)
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
    total = len(manifest.remote_files())
    print(
        f"{total} files of {len(manifest.channels)} channels "
        f"in {FIXTURES_DIR} ({fetched} downloaded, {total - fetched} cached)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
