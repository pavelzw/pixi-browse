from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rattler.package import RunExportsJson
from rattler.version import Version

ViewMode = Literal["packages", "versions", "platforms"]
VersionRowKind = Literal["back", "section", "entry", "empty"]
VersionPreviewKey = tuple[str, str, str, int, str, str]
DependencyTab = Literal["dependencies", "constraints", "run_exports"]
PackageFilePathType = Literal["hardlink", "softlink", "directory"]
CompareLineKind = Literal["added", "removed", "changed"]


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


@dataclass(frozen=True)
class PackageFile:
    path: str
    size_in_bytes: int | None = None
    sha256: bytes | None = None
    no_link: bool | None = None
    path_type: PackageFilePathType | None = None


@dataclass(frozen=True)
class MetadataField:
    label: str
    value: str


@dataclass(frozen=True)
class VersionArtifactData:
    metadata_fields: tuple[MetadataField, ...]
    dependencies: tuple[str, ...]
    constraints: tuple[str, ...]
    run_exports: tuple[str, ...]
    file_paths: tuple[PackageFile, ...] = ()
    raw_run_exports: RunExportsJson | None = None


@dataclass(frozen=True)
class CompareSelection:
    package_name: str
    entry: VersionEntry


@dataclass(frozen=True)
class MetadataDiff:
    label: str
    before: str
    after: str


@dataclass(frozen=True)
class CompareRow:
    label: str
    left: str
    right: str
    changed: bool


@dataclass(frozen=True)
class CompareFileRow:
    label: str
    left: str
    right: str
    changed: bool
    left_file: PackageFile | None = None
    right_file: PackageFile | None = None


@dataclass(frozen=True)
class VersionCompareData:
    left_selection: CompareSelection
    right_selection: CompareSelection
    metadata_rows: tuple[CompareRow, ...]
    dependencies: tuple[CompareRow, ...]
    constraints: tuple[CompareRow, ...]
    run_exports: tuple[CompareRow, ...]
    files: tuple[CompareFileRow, ...]


@dataclass(frozen=True)
class VersionDetailsData:
    metadata_lines: tuple[str, ...]
    dependencies: tuple[str, ...]
    constraints: tuple[str, ...]
    run_exports: tuple[str, ...]
    files: tuple[str, ...]
    file_paths: tuple[PackageFile, ...] = ()
