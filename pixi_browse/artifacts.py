from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pixi_browse.models import (
    ArtifactSource,
    LocalArtifactSource,
    RemoteArtifactSource,
)


class InvalidArtifactSourceError(ValueError):
    pass


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
