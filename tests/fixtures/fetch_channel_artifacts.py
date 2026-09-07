"""Download the real conda-forge artifacts listed in ``channel/artifacts.toml``.

The committed test channel is a handful of small, real conda-forge packages.
This script (re)downloads them and verifies their SHA256 so the fixture data
stays reproducible. Run it with ``pixi run fetch-test-channel`` after adding an
entry to the manifest.
"""

from __future__ import annotations

import hashlib
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CHANNEL_DIR = Path(__file__).resolve().parent / "channel"
MANIFEST_PATH = CHANNEL_DIR / "artifacts.toml"


@dataclass(frozen=True)
class Artifact:
    subdir: str
    file_name: str
    sha256: str

    @property
    def path(self) -> Path:
        return CHANNEL_DIR / self.subdir / self.file_name


def load_manifest() -> tuple[str, list[Artifact]]:
    manifest = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    channel = manifest["channel"]
    assert isinstance(channel, str)
    artifacts = [
        Artifact(
            subdir=str(entry["subdir"]),
            file_name=str(entry["file_name"]),
            sha256=str(entry["sha256"]),
        )
        for entry in manifest["artifacts"]
    ]
    return channel.rstrip("/"), artifacts


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    channel, artifacts = load_manifest()
    failures = 0
    for artifact in artifacts:
        if artifact.path.exists() and sha256_of(artifact.path) == artifact.sha256:
            print(f"ok        {artifact.subdir}/{artifact.file_name}")
            continue

        url = f"{channel}/{artifact.subdir}/{artifact.file_name}"
        print(f"fetching  {url}")
        artifact.path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as response:  # noqa: S310
            data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != artifact.sha256:
            print(
                f"MISMATCH  {artifact.file_name}: expected {artifact.sha256}, got {digest}"
            )
            failures += 1
            continue
        artifact.path.write_bytes(data)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
