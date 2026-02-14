from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rattler.version import Version

ViewMode = Literal["packages", "versions", "platforms"]
VersionRowKind = Literal["back", "section", "entry", "empty"]
VersionPreviewKey = tuple[str, str, str, int, str, str]


@dataclass(frozen=True)
class VersionEntry:
    version: Version
    build: str
    build_number: int
    subdir: str
    file_name: str


@dataclass(frozen=True)
class VersionRow:
    kind: VersionRowKind
    subdir: str | None = None
    entry: VersionEntry | None = None
