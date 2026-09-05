from __future__ import annotations

from rattler.networking import Client
from rattler.package import AboutJson, PathsJson, RunExportsJson
from rattler.package_streaming import (
    download_to_path as package_download_to_path,
)
from rattler.package_streaming import fetch_raw_package_file_from_url

from pixi_browse.repodata import create_gateway

from .app import CondaMetadataTui
from .widgets import (
    ACTIVE_SECTION_TITLE_STYLE,
    ACTIVE_TAB_STYLE,
    DEPENDENCY_TABS,
    EMPTY_MATCHSPEC_RESULT,
    EMPTY_WHONEEDS_RESULT,
    FILE_TABS,
    INACTIVE_SECTION_TITLE_STYLE,
    INACTIVE_SELECTED_TAB_STYLE,
    INACTIVE_TAB_STYLE,
    CompareDetailsView,
    CompareScreen,
    DetailSection,
    DownloadPathScreen,
    Empty,
    FileActionScreen,
    FilePreviewScreen,
    HelpScreen,
    MainPanel,
    MatchSpecScreen,
    SidebarPanel,
    VersionDetailsView,
    WhoNeedsLoadingScreen,
    WhoNeedsScreen,
)

__all__ = [
    "ACTIVE_SECTION_TITLE_STYLE",
    "ACTIVE_TAB_STYLE",
    "AboutJson",
    "Client",
    "CompareDetailsView",
    "CompareScreen",
    "CondaMetadataTui",
    "DEPENDENCY_TABS",
    "DetailSection",
    "DownloadPathScreen",
    "Empty",
    "EMPTY_MATCHSPEC_RESULT",
    "EMPTY_WHONEEDS_RESULT",
    "FILE_TABS",
    "FileActionScreen",
    "FilePreviewScreen",
    "HelpScreen",
    "INACTIVE_SECTION_TITLE_STYLE",
    "INACTIVE_SELECTED_TAB_STYLE",
    "INACTIVE_TAB_STYLE",
    "MainPanel",
    "MatchSpecScreen",
    "PathsJson",
    "RunExportsJson",
    "SidebarPanel",
    "VersionDetailsView",
    "WhoNeedsLoadingScreen",
    "WhoNeedsScreen",
    "create_gateway",
    "fetch_raw_package_file_from_url",
    "package_download_to_path",
]
