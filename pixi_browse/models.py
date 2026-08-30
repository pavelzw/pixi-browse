from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from rattler.package import RunExportsJson
from rattler.repo_data import PackageRecord
from rattler.version import Version

ViewMode = Literal["packages", "versions", "platforms"]
VersionRowKind = Literal["back", "section", "entry", "empty"]
VersionPreviewKey = tuple[str, str, str, int, str, str]
ArtifactCacheKey = VersionPreviewKey | str
DependencyTab = Literal["dependencies", "constraints", "run_exports"]
FileTab = Literal["pkg", "info"]
PackageFilePathType = Literal["hardlink", "softlink", "directory"]
MetadataRow = tuple[str, str]


@dataclass(frozen=True)
class LocalArtifactSource:
    path: Path

    @property
    def cache_key(self) -> str:
        return f"path:{self.path}"

    @property
    def display_name(self) -> str:
        return self.path.name

    @property
    def location(self) -> str:
        return str(self.path)


@dataclass(frozen=True)
class RemoteArtifactSource:
    url: str

    @property
    def cache_key(self) -> str:
        return f"url:{self.url}"

    @property
    def display_name(self) -> str:
        return Path(unquote(urlparse(self.url).path)).name or self.url

    @property
    def location(self) -> str:
        return self.url


ArtifactSource = LocalArtifactSource | RemoteArtifactSource


@dataclass(frozen=True)
class ArtifactDescriptor:
    record: PackageRecord
    source: ArtifactSource
    file_name: str
    channel: str | None = None
    package_name: str | None = None


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
    link_target: str | None = None

    @property
    def is_symlink(self) -> bool:
        return self.path_type == "softlink"


@dataclass(frozen=True)
class VersionArtifactData:
    metadata_rows: tuple[MetadataRow, ...]
    dependencies: tuple[str, ...]
    constraints: tuple[str, ...]
    package_url: str = ""
    file_paths: tuple[PackageFile, ...] = ()
    info_files: tuple[PackageFile, ...] = ()
    run_exports: RunExportsJson | None = None
    repository_urls: tuple[str, ...] = ()
    documentation_urls: tuple[str, ...] = ()
    homepage_urls: tuple[str, ...] = ()
    recipe_maintainers: tuple[str, ...] = ()
    provenance_remote_url: str | None = None
    provenance_sha: str | None = None
    rattler_build_version: str | None = None


@dataclass(frozen=True)
class CompareSelection:
    package_name: str
    entry: VersionEntry
    source: ArtifactSource | None = None


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
    comparison_known: bool = True


@dataclass(frozen=True)
class VersionCompareData:
    left_selection: CompareSelection
    right_selection: CompareSelection
    metadata_rows: tuple[CompareRow, ...]
    dependencies: tuple[CompareRow, ...]
    constraints: tuple[CompareRow, ...]
    run_exports: tuple[CompareRow, ...]
    files: tuple[CompareFileRow, ...]
    info_files: tuple[CompareFileRow, ...] = ()
