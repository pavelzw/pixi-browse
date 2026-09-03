from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from rattler.package import IndexJson
from rattler.repo_data import PackageRecord
from rattler.version import VersionWithSource

from pixi_browse.models import (
    ArtifactSource,
    LocalArtifactSource,
    RemoteArtifactSource,
)


class InvalidArtifactSourceError(ValueError):
    pass


def into_package_record(
    value: IndexJson | PackageRecord,
) -> PackageRecord:
    if isinstance(value, PackageRecord):
        return value

    record = PackageRecord(
        name=value.name,
        # IndexJson exposes Version even though PackageRecord requires
        # VersionWithSource, so convert explicitly at this boundary.
        version=VersionWithSource(str(value.version)),
        build=value.build,
        build_number=value.build_number,
        subdir=value.subdir or "unknown",
        arch=value.arch,
        platform=value.platform,
        depends=list(value.depends),
        constrains=list(value.constrains),
        license=value.license,
        license_family=value.license_family,
    )

    record.timestamp = value.timestamp
    record.features = value.features
    record.track_features = list(value.track_features)
    return record


def parse_artifact_source(value: str) -> ArtifactSource:
    candidate = value.strip()
    if not candidate:
        raise InvalidArtifactSourceError("Artifact source cannot be empty.")

    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"}:
        if not parsed.netloc:
            raise InvalidArtifactSourceError(f"Invalid artifact URL: {candidate}")
        return RemoteArtifactSource(candidate)
    if parsed.scheme:
        raise InvalidArtifactSourceError(
            f"Unsupported artifact URL scheme {parsed.scheme!r}; use HTTP(S) or a path."
        )

    path = Path(candidate).expanduser().resolve()
    if not path.exists():
        raise InvalidArtifactSourceError(f"Artifact does not exist: {path}")
    if not path.is_file():
        raise InvalidArtifactSourceError(f"Artifact is not a file: {path}")
    return LocalArtifactSource(path)
