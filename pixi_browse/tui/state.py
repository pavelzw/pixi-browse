from __future__ import annotations

from dataclasses import dataclass

from rattler.platform import Platform
from rattler.repo_data import RepoDataRecord

from pixi_browse.models import (
    CompareSelection,
    PackageFile,
    VersionArtifactData,
    VersionDetailsData,
    VersionEntry,
    VersionPreviewKey,
    VersionRow,
    ViewMode,
)


@dataclass(frozen=True)
class AboutUrls:
    repository: tuple[str, ...] = ()
    documentation: tuple[str, ...] = ()
    homepage: tuple[str, ...] = ()
    recipe_maintainers: tuple[str, ...] = ()
    provenance_remote_url: str | None = None
    provenance_sha: str | None = None
    rattler_build_version: str | None = None


@dataclass(frozen=True)
class ChannelStateSnapshot:
    channel_name: str
    mode: ViewMode
    draft_selected_platform_names: set[Platform] | None
    current_versions: list[VersionEntry]
    version_subdirs: list[str]
    versions_by_subdir: dict[str, list[VersionEntry]]
    collapsed_version_subdirs: set[str]
    version_rows: list[VersionRow]
    selected_package: str | None
    previewed_version_key: VersionPreviewKey | None
    pending_preview_version_key: VersionPreviewKey | None
    previewed_package: str | None
    pending_preview_package: str | None
    platforms: list[Platform]
    available_platform_names: list[Platform]
    selected_platform_names: set[Platform]
    channel_package_names: list[str]
    all_package_names: list[str]
    visible_package_names: list[str]
    matchspec_query: str
    matchspec_records_by_package: dict[str, list[RepoDataRecord]]
    package_records_cache: dict[str, list[RepoDataRecord]]
    version_about_urls_cache: dict[VersionPreviewKey, AboutUrls]
    version_paths_cache: dict[VersionPreviewKey, list[PackageFile]]
    version_artifact_data_cache: dict[VersionPreviewKey, VersionArtifactData]
    version_details_cache: dict[VersionPreviewKey, VersionDetailsData]
    compare_selection: CompareSelection | None
    last_package_highlight: int | None
    last_package_scroll_y: float
    sidebar_highlight: int | None
    sidebar_scroll_y: float
