from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rattler.package import RunExportsJson
from rattler.repo_data import ChannelNotice
from rattler.version import Version

ViewMode = Literal["packages", "versions", "platforms"]
VersionRowKind = Literal["back", "section", "entry", "empty"]
VersionPreviewKey = tuple[str, str, str, int, str, str]
MetadataTab = Literal["metadata", "patches"]
DependencyTab = Literal["dependencies", "constraints", "run_exports"]
FileTab = Literal["pkg", "info"]
PackageFilePathType = Literal["hardlink", "softlink", "directory"]
MetadataRow = tuple[str, str]
ChannelNoticeLevel = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class ChannelNoticeItem:
    """A CEP-6 channel notice paired with the channel name the user browses.

    Rattler reports the channel of a notice as its base URL. The app shows
    channels under the name (or URL) they were added with, so the URL is
    resolved back to that name once when the notices are fetched.
    """

    channel_name: str
    notice: ChannelNotice

    @property
    def level(self) -> ChannelNoticeLevel:
        return self.notice.level


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
class CompareRow:
    label: str
    left: str
    right: str
    changed: bool


@dataclass(frozen=True)
class RepodataPatchDiff:
    """Differences between a package's ``info/index.json`` and its repodata record.

    Channels can ship repodata patches that rewrite the metadata served by the
    channel without touching the package archive itself. Each row pairs the
    unpatched value from ``index.json`` (``left``) with the value the gateway
    returned from the channel's repodata (``right``). Only changed rows are kept.
    """

    metadata: tuple[CompareRow, ...] = ()
    dependencies: tuple[CompareRow, ...] = ()
    constraints: tuple[CompareRow, ...] = ()

    @property
    def rows(self) -> tuple[CompareRow, ...]:
        return (*self.metadata, *self.dependencies, *self.constraints)

    @property
    def change_count(self) -> int:
        return len(self.rows)

    @property
    def is_patched(self) -> bool:
        return self.change_count > 0


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
    repodata_patches: RepodataPatchDiff = RepodataPatchDiff()


@dataclass(frozen=True)
class CompareSelection:
    package_name: str
    entry: VersionEntry


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
