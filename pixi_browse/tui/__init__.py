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
    INACTIVE_SECTION_TITLE_STYLE,
    INACTIVE_SELECTED_TAB_STYLE,
    INACTIVE_TAB_STYLE,
    DetailSection,
    Empty,
    FileActionScreen,
    FilePreviewScreen,
    HelpScreen,
    MainPanel,
    MatchSpecScreen,
    SidebarPanel,
    VersionDetailsView,
)

__all__ = [
    "ACTIVE_SECTION_TITLE_STYLE",
    "ACTIVE_TAB_STYLE",
    "AboutJson",
    "Client",
    "CondaMetadataTui",
    "DEPENDENCY_TABS",
    "DetailSection",
    "Empty",
    "EMPTY_MATCHSPEC_RESULT",
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
    "create_gateway",
    "fetch_raw_package_file_from_url",
    "package_download_to_path",
]
