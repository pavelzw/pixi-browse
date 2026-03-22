from __future__ import annotations

import webbrowser
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, cast

from rattler.exceptions import GatewayError
from rattler.match_spec import MatchSpec
from rattler.networking import Client
from rattler.package_streaming import (
    download_to_path as package_download_to_path,
)
from rattler.package_streaming import fetch_raw_package_file_from_url
from rattler.platform import Platform
from rattler.repo_data import Gateway, RepoDataRecord
from rattler.version import Version, VersionWithSource
from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key, Paste, Resize
from textual.widgets import OptionList, Static

from pixi_browse import __version__
from pixi_browse.models import (
    CompareSelection,
    DependencyTab,
    PackageFile,
    VersionArtifactData,
    VersionDetailsData,
    VersionEntry,
    VersionPreviewKey,
    VersionRow,
    ViewMode,
)
from pixi_browse.platform_utils import platform_sort_key
from pixi_browse.rendering import (
    build_version_compare_data,
    format_human_byte_size,
    render_package_preview,
)
from pixi_browse.repodata import (
    MatchSpecQueryResult,
    create_gateway,
    discover_available_platforms,
    fetch_package_names,
    query_matchspec_records,
    query_package_records,
)
from pixi_browse.search import fuzzy_score

from .state import AboutUrls, ChannelStateSnapshot
from .version_loader import VersionDataLoader
from .widgets import (
    ACTIVE_SECTION_TITLE_STYLE,
    DEPENDENCY_TABS,
    INACTIVE_SECTION_TITLE_STYLE,
    CompareScreen,
    Empty,
    FileActionScreen,
    FilePreviewScreen,
    HelpScreen,
    MainPanel,
    MatchSpecScreen,
    SidebarPanel,
)

_PREVIEW_MAX_BYTES = 256 * 1024


class CondaMetadataTui(App[None]):
    CSS_PATH = Path(__file__).resolve().parent.parent / "selection_list.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("question_mark", "show_help", "Help", show=False),
        Binding("tab", "tab_key", show=False, priority=True),
        Binding("shift+tab", "backtab_key", show=False, priority=True),
        Binding("p", "platform_key_p", "Platform"),
        Binding("c", "channel_key_c", "Channel"),
        Binding("C", "compare_key_c", "Compare"),
        Binding("m", "matchspec_key_m", "MatchSpec"),
        Binding("slash", "filter_key_slash", show=False),
        Binding("escape", "escape", "Back", show=False),
        Binding("q", "quit_or_type_q", "Quit"),
        Binding("ctrl+c", "quit", show=False),
    ]

    def __init__(
        self,
        *,
        default_channel: str = "conda-forge",
        default_platforms: Iterable[Platform] | None = None,
        default_matchspec: MatchSpec | None = None,
    ) -> None:
        super().__init__()
        channel_name = default_channel.strip() or "conda-forge"
        selected_platforms = set(default_platforms or [])
        self.theme = "textual-ansi"
        self._client = Client.default_client()

        self._gateway: Gateway = create_gateway(client=self._client)
        self._platforms: list[Platform] = []
        self._available_platform_names: list[Platform] = []
        self._selected_platform_names: set[Platform] = set(selected_platforms)
        self._draft_selected_platform_names: set[Platform] | None = None
        self._package_records_cache: dict[str, list[RepoDataRecord]] = {}
        self._channel_name = channel_name
        self._mode: ViewMode = "packages"
        self._search_query = ""
        self._channel_package_names: list[str] = []
        self._all_package_names: list[str] = []
        self._visible_package_names: list[str] = []
        self._startup_matchspec = default_matchspec
        self._matchspec_query = ""
        self._matchspec_records_by_package: dict[str, list[RepoDataRecord]] = {}
        self._current_versions: list[VersionEntry] = []
        self._version_subdirs: list[str] = []
        self._versions_by_subdir: dict[str, list[VersionEntry]] = {}
        self._collapsed_version_subdirs: set[str] = set()
        self._version_rows: list[VersionRow] = []
        self._version_loader = VersionDataLoader(client=self._client)
        self._version_about_urls_cache = self._version_loader.about_urls_cache
        self._version_paths_cache = self._version_loader.paths_cache
        self._version_artifact_data_cache = self._version_loader.artifact_data_cache
        self._version_details_cache = self._version_loader.details_cache
        self._previewed_version_key: VersionPreviewKey | None = None
        self._pending_preview_version_key: VersionPreviewKey | None = None
        self._selected_package: str | None = None
        self._previewed_package: str | None = None
        self._pending_preview_package: str | None = None
        self._filter_mode = False
        self._channel_edit_mode = False
        self._channel_draft = self._channel_name
        self._download_indicator_override: str | None = None
        self._download_in_progress = False
        self._file_action_in_progress = False
        self._last_package_highlight: int | None = None
        self._last_package_scroll_y = 0.0
        self._sidebar_vim_g_pending = False
        self._sidebar_selection_by_keyboard = False
        self._selected_pane: Literal["sidebar", "main"] = "sidebar"
        self._compare_selection: CompareSelection | None = None
        self._compare_screen_open = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with SidebarPanel(id="sidebar"):
                yield OptionList(id="sidebar-list")
                yield Static("Loading repodata...", id="status")
            yield MainPanel(id="main-panel")
        yield Static(self._footer_text(), id="footer")

    async def on_mount(self) -> None:
        package_list = self.query_one("#sidebar-list", OptionList)
        package_list.disabled = True
        package_list.focus()
        self._update_filter_indicator()
        loaded = await self._load_packages()
        if loaded and self._startup_matchspec is not None:
            await self._apply_matchspec_query(self._startup_matchspec)

    async def _load_packages(self) -> bool:
        status = self.query_one("#status", Static)
        status.update("Discovering available platforms via sharded gateway...")
        try:
            await self._ensure_available_platforms()
            status.update(
                f"Downloading repodata for {self._selected_platforms_text()} (sharded)..."
            )
            self._channel_package_names = await self._fetch_package_names_with_gateway()
        except (GatewayError, RuntimeError) as exc:
            status.update(f"Failed to load repodata: {exc!s}")
            return False

        self._all_package_names = list(self._channel_package_names)
        self._visible_package_names = list(self._all_package_names)
        self._render_package_options()

        package_list = self.query_one("#sidebar-list", OptionList)
        package_list.disabled = False
        package_list.focus()
        self._update_platform_indicator()
        self._update_package_selection_status()
        if self._visible_package_names:
            self._request_package_preview(self._visible_package_names[0])
        return True

    async def _discover_available_platforms(self) -> list[Platform]:
        return await discover_available_platforms(
            gateway=self._gateway,
            channel_name=self._channel_name,
        )

    async def _ensure_available_platforms(self) -> None:
        if not self._available_platform_names:
            self._available_platform_names = await self._discover_available_platforms()
        if not self._available_platform_names:
            raise RuntimeError("No reachable platform repodata endpoints found.")

        self._selected_platform_names = {
            platform
            for platform in self._selected_platform_names
            if platform in self._available_platform_names
        }
        if self._selected_platform_names:
            return

        self._selected_platform_names = set(self._available_platform_names)

    async def _fetch_package_names_with_gateway(self) -> list[str]:
        await self._ensure_available_platforms()

        self._platforms, package_names = await fetch_package_names(
            gateway=self._gateway,
            channel_name=self._channel_name,
            selected_platforms=self._selected_platform_names,
        )
        return package_names

    def _render_package_options(self, *, preserve_position: bool = False) -> None:
        package_list = self.query_one("#sidebar-list", OptionList)
        previous_highlight = package_list.highlighted
        previous_scroll_y = package_list.scroll_y
        package_list.clear_options()
        if self._visible_package_names:
            package_list.add_options(self._visible_package_names)
            if preserve_position and previous_highlight is not None:
                package_list.highlighted = min(
                    previous_highlight, len(self._visible_package_names) - 1
                )
                package_list.scroll_to(y=previous_scroll_y, animate=False)
            else:
                package_list.action_first()
            return
        package_list.add_option("No packages found")

    def _version_row_width(self) -> int:
        package_list = self.query_one("#sidebar-list", OptionList)
        return max(16, package_list.size.width - 4)

    def _format_version_option_label(self, entry: VersionEntry, row_width: int) -> str:
        left = str(entry.version)
        right = entry.build
        if row_width <= len(left) + len(right) + 1:
            return f"{left} {right}"
        gap = row_width - len(left) - len(right)
        return f"{left}{' ' * gap}{right}"

    def _render_version_options(self, *, preserve_position: bool = False) -> None:
        package_list = self.query_one("#sidebar-list", OptionList)
        previous_highlight = package_list.highlighted
        previous_scroll_y = package_list.scroll_y
        package_list.clear_options()
        self._version_rows = []

        package_list.add_option("< Back to packages")
        self._version_rows.append(VersionRow(kind="back"))
        if not self._current_versions:
            package_list.add_option("No versions found")
            self._version_rows.append(VersionRow(kind="empty"))
            package_list.action_first()
            return

        row_width = self._version_row_width()
        for subdir in self._version_subdirs:
            subdir_entries = self._versions_by_subdir.get(subdir, [])
            collapsed = subdir in self._collapsed_version_subdirs
            marker = "▸" if collapsed else "▾"
            package_list.add_option(f"{marker} {subdir} ({len(subdir_entries)})")
            self._version_rows.append(VersionRow(kind="section", subdir=subdir))
            if collapsed:
                continue

            package_list.add_options(
                [
                    self._format_version_option_label(entry, row_width)
                    for entry in subdir_entries
                ]
            )
            self._version_rows.extend(
                VersionRow(kind="entry", subdir=subdir, entry=entry)
                for entry in subdir_entries
            )

        if preserve_position and previous_highlight is not None:
            package_list.highlighted = min(
                previous_highlight, len(self._version_rows) - 1
            )
            package_list.scroll_to(y=previous_scroll_y, animate=False)
        else:
            package_list.action_first()

    def _find_version_section_index(self, subdir: str) -> int | None:
        for index, row in enumerate(self._version_rows):
            if row.kind == "section" and row.subdir == subdir:
                return index
        return None

    def _toggle_version_section(self, subdir: str) -> None:
        if subdir in self._collapsed_version_subdirs:
            self._collapsed_version_subdirs.remove(subdir)
        else:
            self._collapsed_version_subdirs.add(subdir)

        package_list = self.query_one("#sidebar-list", OptionList)
        previous_scroll_y = package_list.scroll_y
        self._render_version_options()
        section_index = self._find_version_section_index(subdir)
        if section_index is not None:
            package_list.highlighted = section_index
            package_list.scroll_to(y=previous_scroll_y, animate=False)

    def _update_versions_status(self) -> None:
        self.query_one("#status", Static).update(
            f"{len(self._current_versions):,} entries across "
            f"{len(self._version_subdirs)} platform{'s' if len(self._version_subdirs) > 1 else ''}."
        )

    def _render_platform_options(self) -> None:
        draft = self._draft_selected_platform_names
        if draft is None:
            draft = set(self._selected_platform_names)
            self._draft_selected_platform_names = draft

        package_list = self.query_one("#sidebar-list", OptionList)
        package_list.clear_options()

        if not self._available_platform_names:
            package_list.add_option("No platforms available")
            package_list.action_first()
            return

        package_list.add_options(
            [
                f"✓ {str(platform)}" if platform in draft else f"  {str(platform)}"
                for platform in self._available_platform_names
            ]
        )
        package_list.highlighted = 0

    def _render_sidebar_loading_option(self, label: str) -> None:
        package_list = self.query_one("#sidebar-list", OptionList)
        package_list.clear_options()
        package_list.add_option(label)
        package_list.highlighted = 0

    def _open_platform_selector(self) -> None:
        if self._mode != "packages":
            return

        package_list = self.query_one("#sidebar-list", OptionList)
        self._last_package_highlight = package_list.highlighted
        self._last_package_scroll_y = package_list.scroll_y

        self._mode = "platforms"
        self._draft_selected_platform_names = set(self._selected_platform_names)
        self._render_platform_options()
        self._update_platform_selection_status()
        self._update_platform_indicator()

    def _clear_record_caches(self) -> None:
        self._package_records_cache.clear()
        self._version_loader.clear_caches()

    def _clear_compare_state(self) -> None:
        self._compare_selection = None
        self._compare_screen_open = False
        self._update_footer_if_available()

    def _update_footer_if_available(self) -> None:
        try:
            self.query_one("#footer", Static).update(self._footer_text())
        except Exception:
            pass

    def _reset_preview_state(self) -> None:
        self._previewed_package = None
        self._pending_preview_package = None
        self._previewed_version_key = None
        self._pending_preview_version_key = None

    def _clear_version_state(self) -> None:
        self._current_versions.clear()
        self._version_subdirs.clear()
        self._versions_by_subdir.clear()
        self._collapsed_version_subdirs.clear()
        self._version_rows.clear()
        self._selected_package = None

    def _clear_channel_loaded_state(self) -> None:
        self._mode = "packages"
        self._draft_selected_platform_names = None
        self._clear_version_state()
        self._reset_preview_state()
        self._clear_compare_state()
        self._platforms = []
        self._available_platform_names = []
        self._channel_package_names = []
        self._all_package_names = []
        self._visible_package_names = []
        self._matchspec_query = ""
        self._matchspec_records_by_package = {}
        self._clear_record_caches()

    def _reset_matchspec_selection(self) -> None:
        self._matchspec_query = ""
        self._matchspec_records_by_package = {}
        self._mode = "packages"
        self._draft_selected_platform_names = None
        self._clear_version_state()
        self._reset_preview_state()
        self._clear_compare_state()
        self._all_package_names = list(self._channel_package_names)

    async def _query_matchspec_records(
        self, matchspec: MatchSpec
    ) -> MatchSpecQueryResult:
        return await query_matchspec_records(
            gateway=self._gateway,
            channel_name=self._channel_name,
            platforms=self._platforms,
            matchspec=matchspec,
            record_sort_key=self._record_sort_key,
        )

    async def _reapply_active_matchspec(self) -> None:
        if not self._matchspec_query:
            return
        result = await self._query_matchspec_records(
            MatchSpec(self._matchspec_query, exact_names_only=False)
        )
        await self._apply_matchspec_result(self._matchspec_query, result)

    def _snapshot_channel_state(self) -> ChannelStateSnapshot:
        package_list = self.query_one("#sidebar-list", OptionList)
        return ChannelStateSnapshot(
            channel_name=self._channel_name,
            mode=self._mode,
            draft_selected_platform_names=(
                set(self._draft_selected_platform_names)
                if self._draft_selected_platform_names is not None
                else None
            ),
            current_versions=list(self._current_versions),
            version_subdirs=list(self._version_subdirs),
            versions_by_subdir={
                subdir: list(entries)
                for subdir, entries in self._versions_by_subdir.items()
            },
            collapsed_version_subdirs=set(self._collapsed_version_subdirs),
            version_rows=list(self._version_rows),
            selected_package=self._selected_package,
            previewed_version_key=self._previewed_version_key,
            pending_preview_version_key=self._pending_preview_version_key,
            previewed_package=self._previewed_package,
            pending_preview_package=self._pending_preview_package,
            platforms=list(self._platforms),
            available_platform_names=list(self._available_platform_names),
            selected_platform_names=set(self._selected_platform_names),
            channel_package_names=list(self._channel_package_names),
            all_package_names=list(self._all_package_names),
            visible_package_names=list(self._visible_package_names),
            matchspec_query=self._matchspec_query,
            matchspec_records_by_package={
                package_name: list(records)
                for package_name, records in self._matchspec_records_by_package.items()
            },
            package_records_cache={
                package_name: list(records)
                for package_name, records in self._package_records_cache.items()
            },
            version_about_urls_cache=dict(self._version_about_urls_cache),
            version_paths_cache={
                preview_key: list(paths)
                for preview_key, paths in self._version_paths_cache.items()
            },
            version_artifact_data_cache=dict(self._version_artifact_data_cache),
            version_details_cache=dict(self._version_details_cache),
            compare_selection=self._compare_selection,
            last_package_highlight=self._last_package_highlight,
            last_package_scroll_y=self._last_package_scroll_y,
            sidebar_highlight=package_list.highlighted,
            sidebar_scroll_y=package_list.scroll_y,
        )

    def _restore_channel_state(self, snapshot: ChannelStateSnapshot) -> None:
        self._channel_name = snapshot.channel_name
        self._mode = snapshot.mode
        self._draft_selected_platform_names = snapshot.draft_selected_platform_names
        self._current_versions = snapshot.current_versions
        self._version_subdirs = snapshot.version_subdirs
        self._versions_by_subdir = snapshot.versions_by_subdir
        self._collapsed_version_subdirs = snapshot.collapsed_version_subdirs
        self._version_rows = snapshot.version_rows
        self._selected_package = snapshot.selected_package
        self._previewed_version_key = snapshot.previewed_version_key
        self._pending_preview_version_key = snapshot.pending_preview_version_key
        self._previewed_package = snapshot.previewed_package
        self._pending_preview_package = snapshot.pending_preview_package
        self._platforms = snapshot.platforms
        self._available_platform_names = snapshot.available_platform_names
        self._selected_platform_names = snapshot.selected_platform_names
        self._channel_package_names = snapshot.channel_package_names
        self._all_package_names = snapshot.all_package_names
        self._visible_package_names = snapshot.visible_package_names
        self._matchspec_query = snapshot.matchspec_query
        self._matchspec_records_by_package = snapshot.matchspec_records_by_package
        self._package_records_cache = snapshot.package_records_cache
        self._version_loader.restore_caches(
            about_urls_cache=snapshot.version_about_urls_cache,
            paths_cache=snapshot.version_paths_cache,
            artifact_data_cache=snapshot.version_artifact_data_cache,
            details_cache=snapshot.version_details_cache,
        )
        self._compare_selection = snapshot.compare_selection
        self._compare_screen_open = False
        self._last_package_highlight = snapshot.last_package_highlight
        self._last_package_scroll_y = snapshot.last_package_scroll_y

    def _restore_ui_from_snapshot(self, snapshot: ChannelStateSnapshot) -> None:
        package_list = self.query_one("#sidebar-list", OptionList)
        package_list.disabled = False

        if self._mode == "packages":
            self._render_package_options()
            self._update_package_selection_status()
        elif self._mode == "versions":
            self._render_version_options()
            self._update_versions_status()
        else:
            self._render_platform_options()
            self._update_platform_selection_status()

        option_count = self._sidebar_option_count()
        if snapshot.sidebar_highlight is not None and option_count > 0:
            package_list.highlighted = min(snapshot.sidebar_highlight, option_count - 1)
            package_list.scroll_to(y=snapshot.sidebar_scroll_y, animate=False)
            self._update_main_panel_for_sidebar_highlight(package_list.highlighted)

        self._update_filter_indicator()

    async def _apply_platform_selection(self) -> None:
        selected = set(
            self._draft_selected_platform_names or self._selected_platform_names
        )
        selected = {
            platform
            for platform in selected
            if platform in self._available_platform_names
        }
        if not selected:
            self.query_one("#status", Static).update(
                "Select at least one platform before applying."
            )
            return

        if selected == self._selected_platform_names:
            self._draft_selected_platform_names = None
            self._back_to_packages()
            return

        previous_state = self._snapshot_channel_state()
        self._selected_platform_names = set(selected)
        self._draft_selected_platform_names = None
        self._update_platform_indicator()
        self.query_one("#status", Static).update(
            f"Loading repodata for {self._selected_platforms_text()}..."
        )

        self._clear_compare_state()
        self._clear_record_caches()
        self._reset_preview_state()

        try:
            self._channel_package_names = await self._fetch_package_names_with_gateway()
            self._all_package_names = list(self._channel_package_names)
            if self._matchspec_query:
                await self._reapply_active_matchspec()
            else:
                self._mode = "packages"
                self._filter_packages()
        except (GatewayError, RuntimeError) as exc:
            self._restore_channel_state(previous_state)
            self._restore_ui_from_snapshot(previous_state)
            self.query_one("#status", Static).update(
                f"Failed to load selected platforms: {exc!s}"
            )
            return

        self._update_filter_indicator()
        self.query_one("#sidebar-list", OptionList).focus()

    async def _apply_channel_selection(self, channel_name: str) -> None:
        channel_name = channel_name.strip()
        if not channel_name:
            return

        if channel_name == self._channel_name:
            self._update_filter_indicator()
            return

        previous_state = self._snapshot_channel_state()
        self._channel_name = channel_name
        self._clear_channel_loaded_state()

        package_list = self.query_one("#sidebar-list", OptionList)
        self._render_sidebar_loading_option("Loading packages...")
        package_list.disabled = True
        self._show_main_placeholder(f"# {escape(channel_name)}\n\nLoading repodata...")
        self._update_filter_indicator()

        loaded = await self._load_packages()
        if not loaded:
            self._restore_channel_state(previous_state)
            self._restore_ui_from_snapshot(previous_state)
            package_list.focus()
            self.notify(
                f"Failed to load channel: {channel_name}",
                title="Channel",
                severity="error",
            )
            return

        self.notify(f"Switched to channel: {channel_name}", title="Channel")

    def _toggle_platform_at_index(self, platform_index: int) -> None:
        if platform_index < 0 or platform_index >= len(self._available_platform_names):
            return

        platform = self._available_platform_names[platform_index]
        draft = self._draft_selected_platform_names
        if draft is None:
            draft = set(self._selected_platform_names)
            self._draft_selected_platform_names = draft

        if platform in draft and len(draft) == 1:
            self.query_one("#status", Static).update(
                "At least one platform must remain selected."
            )
            return

        if platform in draft:
            draft.remove(platform)
        else:
            draft.add(platform)

        self._render_platform_options()
        package_list = self.query_one("#sidebar-list", OptionList)
        package_list.highlighted = platform_index
        self._update_platform_indicator()
        self._update_platform_selection_status()

    def _update_package_selection_status(self) -> None:
        self.query_one("#status", Static).update(
            f"{len(self._visible_package_names):,} packages in selection."
        )

    def _record_sort_key(
        self, record: RepoDataRecord
    ) -> tuple[VersionWithSource, str, str, int]:
        return (record.version, record.build, record.subdir, record.build_number)

    async def _get_package_records(self, package_name: str) -> list[RepoDataRecord]:
        cached = self._package_records_cache.get(package_name)
        if cached is not None:
            return cached

        records = await query_package_records(
            gateway=self._gateway,
            channel_name=self._channel_name,
            platforms=self._platforms,
            package_name=package_name,
            record_sort_key=self._record_sort_key,
        )
        self._package_records_cache[package_name] = records
        return records

    async def _get_current_package_records(
        self, package_name: str
    ) -> list[RepoDataRecord]:
        matchspec_records = self._matchspec_records_by_package.get(package_name)
        if matchspec_records is not None:
            return matchspec_records
        return await self._get_package_records(package_name)

    def _show_main_placeholder(self, content: str | Text) -> None:
        self.query_one("#main-panel", MainPanel).show_placeholder(content)

    def _show_version_details(self, details: VersionDetailsData) -> None:
        self.query_one("#main-panel", MainPanel).show_version_details(details)

    def _set_active_main_section(self, index: int) -> None:
        self.query_one("#main-panel", MainPanel).set_active_section(index)

    def _cycle_active_main_section(self, direction: int) -> None:
        self.query_one("#main-panel", MainPanel).cycle_active_section(direction)

    def _set_main_dependency_tab(self, tab: DependencyTab) -> None:
        self.query_one("#main-panel", MainPanel).set_dependency_tab(tab)

    def _cycle_main_dependency_tab(self, direction: int) -> None:
        self.query_one("#main-panel", MainPanel).cycle_dependency_tab(direction)

    def _selected_dependency_matchspec(self) -> str | None:
        return self.query_one("#main-panel", MainPanel).selected_dependency_matchspec()

    def _dependency_matchspec_at(self, index: int) -> str | None:
        return self.query_one("#main-panel", MainPanel).dependency_matchspec_at(index)

    def _selected_file_path(self) -> str | None:
        return self.query_one("#main-panel", MainPanel).selected_file_path()

    def _selected_file_size_in_bytes(self) -> int | None:
        return self.query_one("#main-panel", MainPanel).selected_file_size_in_bytes()

    def _file_path_at(self, index: int) -> str | None:
        return self.query_one("#main-panel", MainPanel).file_path_at(index)

    def _file_size_at(self, index: int) -> int | None:
        return self.query_one("#main-panel", MainPanel).file_size_at(index)

    def _open_matchspec_screen(
        self, initial_value: str, *, select_on_focus: bool = True
    ) -> None:
        self.push_screen(
            MatchSpecScreen(initial_value, select_on_focus=select_on_focus),
            self._handle_matchspec_result,
        )

    def _defer_matchspec_screen(self, initial_value: str) -> None:
        self.call_after_refresh(
            lambda: self._open_matchspec_screen(initial_value, select_on_focus=False)
        )

    def _set_selected_pane(self, pane: Literal["sidebar", "main"]) -> None:
        self._selected_pane = pane
        self._update_filter_indicator()

    def _focus_main_panel(self) -> None:
        self._selected_pane = "main"
        self.query_one("#main-panel", MainPanel).focus()
        self._update_filter_indicator()

    def _focus_sidebar(self) -> None:
        self._selected_pane = "sidebar"
        self.query_one("#sidebar-list", OptionList).focus()
        self._update_filter_indicator()

    def _sidebar_is_focused(self) -> bool:
        return self.focused is self.query_one("#sidebar-list", OptionList)

    def _main_panel_is_focused(self) -> bool:
        return self.focused is self.query_one("#main-panel", MainPanel)

    def _main_panel_shows_version_details(self) -> bool:
        return self.query_one("#main-panel", MainPanel).showing_version_details()

    def _reset_main_panel_scroll(self) -> None:
        self.query_one("#main-panel", MainPanel).reset_scroll()

    def _sidebar_option_count(self) -> int:
        if self._mode == "packages":
            return len(self._visible_package_names)
        if self._mode == "versions":
            return len(self._version_rows)
        if self._mode == "platforms":
            return len(self._available_platform_names)
        return 0

    def _set_sidebar_highlight(self, index: int) -> None:
        option_count = self._sidebar_option_count()
        if option_count <= 0:
            return
        package_list = self.query_one("#sidebar-list", OptionList)
        highlighted = max(0, min(index, option_count - 1))
        package_list.highlighted = highlighted
        self._update_main_panel_for_sidebar_highlight(highlighted)

    def _move_sidebar_highlight(self, delta: int) -> None:
        option_count = self._sidebar_option_count()
        if option_count <= 0:
            return
        package_list = self.query_one("#sidebar-list", OptionList)
        current = package_list.highlighted
        if current is None:
            current = 0
        self._set_sidebar_highlight(current + delta)

    def _page_sidebar(self, direction: int) -> None:
        page_size = max(
            1,
            (self.query_one("#sidebar-list", OptionList).size.height - 2) // 2,
        )
        self._move_sidebar_highlight(direction * page_size)

    def _jump_sidebar_first(self) -> None:
        self._set_sidebar_highlight(0)

    def _jump_sidebar_last(self) -> None:
        self._set_sidebar_highlight(self._sidebar_option_count() - 1)

    def _reset_sidebar_vim_pending(self) -> None:
        self._sidebar_vim_g_pending = False

    @staticmethod
    def _format_help_section(
        title: str, rows: list[tuple[str, str]], *, key_width: int = 18
    ) -> list[str]:
        lines = [title]
        lines.extend(f"  {key:<{key_width}}{description}" for key, description in rows)
        return lines

    def _help_text(self) -> str:
        navigation = self._format_help_section(
            "Navigation",
            [
                ("j / k", "Move selection or scroll"),
                ("h / l", "Focus left / right pane"),
                ("1 / 2 / 3", "Focus metadata, deps, or files"),
                ("Tab / Shift+Tab", "Cycle focused section"),
                ("x", "Swap compare left / right"),
                ("[ / ]", "Cycle dependency tabs"),
                ("gg / G", "Jump to top / bottom"),
                ("Ctrl+u / Ctrl+d", "Page up / down"),
                ("Enter", "Open / select"),
                ("Esc", "Back or close current overlay"),
            ],
        )
        app = self._format_help_section(
            "App",
            [
                ("?", "Show this help"),
                ("/", "Start package filter"),
                ("p", "Open platform selector"),
                ("c", "Edit channel"),
                ("C", "Compare selected artifact in versions view"),
                ("m", "Query MatchSpec"),
                ("d", "Download selected artifact in versions view"),
                ("q", "Quit"),
            ],
        )
        return "\n".join([*navigation, "", *app])

    @staticmethod
    def _extract_rattler_build_version(rendered_recipe_text: str) -> str | None:
        return VersionDataLoader.extract_rattler_build_version(rendered_recipe_text)

    async def _get_record_for_version_entry(
        self, package_name: str, entry: VersionEntry
    ) -> RepoDataRecord | None:
        for record in await self._get_current_package_records(package_name):
            if (
                record.version == entry.version
                and record.build == entry.build
                and record.build_number == entry.build_number
                and record.subdir == entry.subdir
                and record.file_name == entry.file_name
            ):
                return record
        return None

    async def _package_url_for_version_entry(
        self, package_name: str, entry: VersionEntry
    ) -> str:
        record = await self._get_record_for_version_entry(package_name, entry)
        if record is not None:
            return str(record.url)

        channel_base = self._channel_name.rstrip("/")
        if "://" not in channel_base:
            channel_base = f"https://conda.anaconda.org/{channel_base}"
        return f"{channel_base}/{entry.subdir}/{entry.file_name}"

    @staticmethod
    def _file_destination_path(file_path: str) -> Path:
        relative_path = Path(file_path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RuntimeError(f"Unsafe package file path: {file_path}")
        cwd = Path.cwd().resolve()
        destination = (cwd / relative_path).resolve()
        try:
            destination.relative_to(cwd)
        except ValueError as exc:
            raise RuntimeError(f"Unsafe package file path: {file_path}") from exc
        return destination

    def _highlighted_version_entry(self) -> VersionEntry | None:
        row = self._highlighted_version_row()
        if row is None or row.kind != "entry" or row.entry is None:
            return None
        return row.entry

    def _version_preview_key(
        self, package_name: str, entry: VersionEntry
    ) -> VersionPreviewKey:
        return (
            package_name,
            str(entry.version),
            entry.build,
            entry.build_number,
            entry.subdir,
            entry.file_name,
        )

    @staticmethod
    def _compare_selection_label(selection: CompareSelection) -> str:
        entry = selection.entry
        return (
            f"{selection.package_name} {entry.version} {entry.build} [{entry.subdir}]"
        )

    def _compare_selection_key(self, selection: CompareSelection) -> VersionPreviewKey:
        return self._version_preview_key(selection.package_name, selection.entry)

    def _current_compare_selection(self) -> CompareSelection | None:
        row = self._highlighted_version_row()
        if row is None or row.kind != "entry" or row.entry is None:
            return None
        package_name = self._selected_package
        if package_name is None:
            return None
        return CompareSelection(package_name=package_name, entry=row.entry)

    async def _get_package_paths(
        self, preview_key: VersionPreviewKey, url: str
    ) -> list[PackageFile]:
        return await self._version_loader.get_package_paths(preview_key, url)

    async def _get_about_urls(
        self, preview_key: VersionPreviewKey, url: str
    ) -> AboutUrls:
        return await self._version_loader.get_about_urls(preview_key, url)

    async def _load_version_artifact_data(
        self, package_name: str, entry: VersionEntry
    ) -> VersionArtifactData | None:
        record = await self._get_record_for_version_entry(package_name, entry)
        if record is None:
            return None
        preview_key = self._version_preview_key(package_name, entry)
        return await self._version_loader.load_version_artifact_data(
            package_name,
            record,
            preview_key=preview_key,
        )

    def _handle_compare_screen_dismissed(self, _result: None) -> None:
        self._clear_compare_state()

    async def _open_compare_screen(
        self, left_selection: CompareSelection, right_selection: CompareSelection
    ) -> None:
        left_artifact = await self._load_version_artifact_data(
            left_selection.package_name,
            left_selection.entry,
        )
        right_artifact = await self._load_version_artifact_data(
            right_selection.package_name,
            right_selection.entry,
        )

        if left_artifact is None or right_artifact is None:
            self.notify(
                "Unable to load one of the selected artifacts for comparison.",
                title="Compare",
                severity="error",
            )
            return

        if self._compare_selection != left_selection:
            return
        if self._compare_screen_open:
            return

        compare_data = build_version_compare_data(
            left_selection,
            left_artifact,
            right_selection,
            right_artifact,
        )
        self._compare_screen_open = True
        self._update_footer_if_available()
        self.push_screen(
            CompareScreen(compare_data),
            self._handle_compare_screen_dismissed,
        )

    async def _load_and_render_selected_version_preview(
        self, package_name: str, entry: VersionEntry, preview_key: VersionPreviewKey
    ) -> None:
        record = await self._get_record_for_version_entry(package_name, entry)
        if self._mode != "versions":
            return
        if self._pending_preview_version_key != preview_key:
            return

        if record is None:
            self._show_main_placeholder(
                "No matching repodata record found for selected version."
            )
            self._previewed_version_key = None
            return

        details = await self._version_loader.load_version_details(
            package_name,
            record,
            preview_key=preview_key,
        )
        if self._mode != "versions":
            return
        if self._pending_preview_version_key != preview_key:
            return

        self._show_version_details(details)
        self._reset_main_panel_scroll()
        self._previewed_version_key = preview_key

    def _request_selected_version_preview(
        self, package_name: str, entry: VersionEntry
    ) -> None:
        preview_key = self._version_preview_key(package_name, entry)
        self._pending_preview_version_key = preview_key

        if self._previewed_version_key == preview_key:
            return

        cached = self._version_details_cache.get(preview_key)
        if cached is not None:
            self._show_version_details(cached)
            self._reset_main_panel_scroll()
            self._previewed_version_key = preview_key
            return

        self._show_main_placeholder(
            f"# {escape(package_name)} {escape(str(entry.version))}\n\n"
            "Loading repodata for selected version..."
        )
        self._reset_main_panel_scroll()
        self.run_worker(
            self._load_and_render_selected_version_preview(
                package_name, entry, preview_key
            ),
            group="version-preview",
            exclusive=True,
            exit_on_error=False,
        )

    async def _download_selected_version_entry(
        self, package_name: str, entry: VersionEntry
    ) -> None:
        self._download_in_progress = True
        self._set_download_indicator(f"Downloading {entry.file_name}...")

        temporary_destination: Path | None = None
        try:
            url = await self._package_url_for_version_entry(package_name, entry)
            destination = (Path.cwd() / entry.file_name).resolve()
            temporary_destination = destination.with_name(f"{destination.name}.part")

            await package_download_to_path(self._client, url, temporary_destination)
            temporary_destination.replace(destination)
        except Exception as exc:
            if temporary_destination is not None:
                temporary_destination.unlink(missing_ok=True)
            self.notify(
                f"Download failed for {entry.file_name}: {exc!s}",
                title="Download",
                severity="error",
            )
            return
        finally:
            self._download_in_progress = False
            self._set_download_indicator(None)

        if self._mode == "versions":
            self._update_versions_status()
        self.notify(
            f"Downloaded successfully to {destination}",
            title="Download",
        )

    def _request_download_for_highlighted_entry(self) -> None:
        if self._mode != "versions":
            return
        if self._download_in_progress:
            return
        package_name = self._selected_package
        if package_name is None:
            return

        row = self._highlighted_version_row()
        if row is None or row.kind != "entry" or row.entry is None:
            self.notify(
                "Select a specific version entry to download.",
                title="Download",
                severity="warning",
            )
            return

        self._download_in_progress = True
        try:
            self.run_worker(
                self._download_selected_version_entry(package_name, row.entry),
                group="version-download",
                exclusive=True,
                exit_on_error=False,
            )
        except Exception:
            self._download_in_progress = False
            raise

    def _open_file_action_screen(
        self,
        package_name: str,
        entry: VersionEntry,
        file_path: str,
        size_in_bytes: int | None,
    ) -> None:
        self.push_screen(
            FileActionScreen(file_path),
            lambda action: self._handle_file_action_result(
                package_name, entry, file_path, size_in_bytes, action
            ),
        )

    def _defer_file_action_screen(
        self,
        package_name: str,
        entry: VersionEntry,
        file_path: str,
        size_in_bytes: int | None,
    ) -> None:
        self.call_after_refresh(
            lambda: self._open_file_action_screen(
                package_name, entry, file_path, size_in_bytes
            )
        )

    def _request_file_action_for_selected_file(self) -> None:
        if self._mode != "versions" or self._file_action_in_progress:
            return

        file_path = self._selected_file_path()
        if file_path is None:
            return

        size_in_bytes = self._selected_file_size_in_bytes()
        package_name = self._selected_package
        entry = self._highlighted_version_entry()
        assert package_name is not None
        assert entry is not None
        self._defer_file_action_screen(package_name, entry, file_path, size_in_bytes)

    async def _fetch_package_file_bytes(
        self, package_name: str, entry: VersionEntry, file_path: str
    ) -> bytes:
        url = await self._package_url_for_version_entry(package_name, entry)

        return await fetch_raw_package_file_from_url(self._client, url, file_path)

    @staticmethod
    def _preview_content(
        file_path: str,
        package_bytes: bytes | None,
        *,
        size_in_bytes: int | None = None,
    ) -> str:
        if size_in_bytes is None and package_bytes is not None:
            size_in_bytes = len(package_bytes)

        if size_in_bytes is not None and size_in_bytes > _PREVIEW_MAX_BYTES:
            return (
                "File too large to preview in-app "
                f"({size_in_bytes:,} bytes).\n\n"
                "Use Download as file instead."
            )

        assert package_bytes is not None
        if b"\0" in package_bytes:
            return (
                "Binary file preview is not supported.\n\nUse Download as file instead."
            )

        try:
            return package_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return (
                "Binary file preview is not supported.\n\nUse Download as file instead."
            )

    @staticmethod
    def _preview_title(
        file_path: str,
        package_bytes: bytes | None = None,
        *,
        size_in_bytes: int | None = None,
    ) -> str:
        if size_in_bytes is None and package_bytes is not None:
            size_in_bytes = len(package_bytes)
        if size_in_bytes is None:
            return file_path
        return f"{file_path} ({format_human_byte_size(size_in_bytes)})"

    async def _download_selected_package_file(
        self, package_name: str, entry: VersionEntry, file_path: str
    ) -> None:
        temporary_destination: Path | None = None
        try:
            destination = self._file_destination_path(file_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_destination = destination.with_name(f"{destination.name}.part")
            temporary_destination.write_bytes(
                await self._fetch_package_file_bytes(package_name, entry, file_path)
            )
            temporary_destination.replace(destination)
        except Exception as exc:
            if temporary_destination is not None:
                temporary_destination.unlink(missing_ok=True)
            self.notify(
                f"Failed to download {file_path}: {exc!s}",
                title="Files",
                severity="error",
            )
            return

        self.notify(
            f"Downloaded file to {destination}",
            title="Files",
        )

    async def _preview_selected_package_file(
        self,
        package_name: str,
        entry: VersionEntry,
        file_path: str,
        size_in_bytes: int | None = None,
    ) -> None:
        try:
            if size_in_bytes is not None and size_in_bytes > _PREVIEW_MAX_BYTES:
                self.push_screen(
                    FilePreviewScreen(
                        self._preview_title(file_path, size_in_bytes=size_in_bytes),
                        self._preview_content(
                            file_path,
                            None,
                            size_in_bytes=size_in_bytes,
                        ),
                    )
                )
                return
            package_bytes = await self._fetch_package_file_bytes(
                package_name, entry, file_path
            )
            self.push_screen(
                FilePreviewScreen(
                    self._preview_title(file_path, package_bytes),
                    self._preview_content(file_path, package_bytes),
                )
            )
        except Exception as exc:
            self.notify(
                f"Failed to preview {file_path}: {exc!s}",
                title="Files",
                severity="error",
            )

    async def _run_file_action(
        self,
        package_name: str,
        entry: VersionEntry,
        file_path: str,
        size_in_bytes: int | None,
        action: Literal["download", "preview"],
    ) -> None:
        try:
            if action == "download":
                await self._download_selected_package_file(
                    package_name, entry, file_path
                )
                return
            if action == "preview":
                await self._preview_selected_package_file(
                    package_name, entry, file_path, size_in_bytes
                )
                return
        finally:
            self._file_action_in_progress = False

    def _handle_file_action_result(
        self,
        package_name: str,
        entry: VersionEntry,
        file_path: str,
        size_in_bytes: int | None,
        action: Literal["download", "preview"] | None,
    ) -> None:
        if action is None or self._file_action_in_progress:
            return

        self._file_action_in_progress = True
        try:
            self.run_worker(
                self._run_file_action(
                    package_name, entry, file_path, size_in_bytes, action
                ),
                group="file-action",
                exclusive=True,
                exit_on_error=False,
            )
        except Exception:
            self._file_action_in_progress = False
            raise

    def _render_package_preview(
        self, package_name: str, records: list[RepoDataRecord]
    ) -> str:
        return render_package_preview(
            package_name,
            records,
            record_sort_key=self._record_sort_key,
        )

    def _update_main_panel_for_package(
        self, package_name: str, records: list[RepoDataRecord]
    ) -> None:
        self._show_main_placeholder(self._render_package_preview(package_name, records))
        self._reset_main_panel_scroll()
        self._previewed_package = package_name

    async def _load_and_render_package_preview(self, package_name: str) -> None:
        records = await self._get_current_package_records(package_name)
        if self._mode != "packages":
            return
        if self._pending_preview_package != package_name:
            return
        self._update_main_panel_for_package(package_name, records)

    def _request_package_preview(self, package_name: str) -> None:
        self._pending_preview_package = package_name
        if self._previewed_package == package_name:
            return
        matchspec_records = self._matchspec_records_by_package.get(package_name)
        if matchspec_records is not None:
            self._update_main_panel_for_package(package_name, matchspec_records)
            return
        cached = self._package_records_cache.get(package_name)
        if cached is not None:
            self._update_main_panel_for_package(package_name, cached)
            return

        self._show_main_placeholder(f"# {package_name}\n\nLoading repodata...")
        self.run_worker(
            self._load_and_render_package_preview(package_name),
            group="package-preview",
            exclusive=True,
            exit_on_error=False,
        )

    def _filter_packages(self) -> None:
        if not self._filter_mode or not self._search_query:
            self._visible_package_names = list(self._all_package_names)
        else:
            scored_results: list[tuple[int, str]] = []
            for package_name in self._all_package_names:
                score = fuzzy_score(self._search_query, package_name)
                if score is not None:
                    scored_results.append((score, package_name))

            scored_results.sort(key=lambda item: (-item[0], item[1]))
            self._visible_package_names = [
                package_name for _, package_name in scored_results
            ]
        self._render_package_options()
        self._update_package_selection_status()
        self._previewed_package = None
        if self._visible_package_names:
            self._request_package_preview(self._visible_package_names[0])
        else:
            self._pending_preview_package = None
            self._show_main_placeholder("No packages match the current selection.")

    async def _apply_matchspec_result(
        self, query: str, result: MatchSpecQueryResult
    ) -> None:
        self._matchspec_query = query
        self._matchspec_records_by_package = {
            package_name: list(records)
            for package_name, records in result.records_by_package.items()
        }
        self._filter_mode = False
        self._search_query = ""
        self._mode = "packages"
        self._draft_selected_platform_names = None
        self._clear_version_state()
        self._reset_preview_state()
        self._all_package_names = list(result.package_names)
        self._filter_packages()
        self._update_filter_indicator()
        if (
            len(result.package_names) == 1
            and result.package_names[0] in self._visible_package_names
        ):
            await self._open_versions(result.package_names[0])
            self._focus_sidebar()
            return
        self._focus_sidebar()

    async def _apply_matchspec_query(self, matchspec: MatchSpec | None) -> None:
        if matchspec is None:
            self._reset_matchspec_selection()
            self._filter_packages()
            self._update_filter_indicator()
            self._focus_sidebar()
            return

        query = str(matchspec)

        previous_state = self._snapshot_channel_state()
        package_list = self.query_one("#sidebar-list", OptionList)
        package_list.disabled = True
        self._render_sidebar_loading_option("Querying MatchSpec...")
        self._show_main_placeholder(
            f"# MatchSpec\n\nRunning query for `{escape(query)}`..."
        )
        try:
            result = await self._query_matchspec_records(matchspec)
        except (GatewayError, RuntimeError) as exc:
            self._restore_channel_state(previous_state)
            self._restore_ui_from_snapshot(previous_state)
            package_list.focus()
            self.notify(
                f"Failed to query MatchSpec: {exc!s}",
                title="MatchSpec",
                severity="error",
            )
            return

        package_list.disabled = False
        await self._apply_matchspec_result(query, result)

    def _back_to_packages(self) -> None:
        self._mode = "packages"
        self._draft_selected_platform_names = None
        self._clear_version_state()
        self._previewed_version_key = None
        self._pending_preview_version_key = None
        self._filter_packages()
        self._update_filter_indicator()
        package_list = self.query_one("#sidebar-list", OptionList)
        if self._visible_package_names and self._last_package_highlight is not None:
            package_list.highlighted = min(
                self._last_package_highlight, len(self._visible_package_names) - 1
            )
            package_list.scroll_to(y=self._last_package_scroll_y, animate=False)
            highlighted = package_list.highlighted
            if highlighted is not None:
                self._request_package_preview(self._visible_package_names[highlighted])
        self._focus_sidebar()

    async def _open_versions(self, package_name: str) -> None:
        package_list = self.query_one("#sidebar-list", OptionList)
        self._last_package_highlight = package_list.highlighted
        self._last_package_scroll_y = package_list.scroll_y

        self._selected_package = package_name
        self._current_versions = self._build_version_entries(
            await self._get_current_package_records(package_name)
        )
        grouped_versions: dict[str, list[VersionEntry]] = defaultdict(list)
        for entry in self._current_versions:
            grouped_versions[entry.subdir].append(entry)
        self._version_subdirs = sorted(
            grouped_versions,
            key=lambda subdir: (subdir == "noarch", subdir),
        )
        self._versions_by_subdir = {
            subdir: grouped_versions[subdir] for subdir in self._version_subdirs
        }
        self._collapsed_version_subdirs.clear()
        self._mode = "versions"
        self._update_filter_indicator()
        self._render_version_options()
        self._update_versions_status()
        self._previewed_version_key = None
        self._pending_preview_version_key = None
        self._pending_preview_package = None
        self._previewed_package = package_name

    def _build_version_entries(
        self, records: list[RepoDataRecord]
    ) -> list[VersionEntry]:
        versions_by_key: dict[tuple[Version, str, int, str, str], VersionEntry] = {}
        for record in records:
            key = (
                record.version,
                record.build,
                record.build_number,
                record.subdir,
                record.file_name,
            )
            versions_by_key[key] = VersionEntry(
                version=record.version,
                build=record.build,
                build_number=record.build_number,
                subdir=record.subdir,
                file_name=record.file_name,
            )

        return sorted(
            versions_by_key.values(),
            key=lambda entry: (
                entry.version,
                entry.build,
                entry.subdir,
                entry.build_number,
                entry.file_name,
            ),
            reverse=True,
        )

    def _footer_text(self) -> str:
        if self._channel_edit_mode:
            return f"Channel: {self._channel_draft}_"

        if self._mode == "packages" and self._filter_mode:
            return f"Search: {self._search_query}_"

        if self._compare_screen_open:
            return (
                "Compare: Tab/Shift+Tab panes | Swap: x | Back: esc | Quit: q | Help: ?"
            )

        footer = "Search: / | Platform: p | Channel: c | MatchSpec: m"
        if self._mode == "versions":
            footer += f" | Compare: C | {self._download_indicator_text().plain}"
        footer += " | Help: ?"
        return footer

    def _sidebar_title_text(self, *, selected: bool) -> Text:
        if self._mode == "versions":
            label = (
                f"Versions: {self._selected_package}"
                if self._selected_package is not None
                else "Versions"
            )
        elif self._mode == "platforms":
            label = "Platforms"
        else:
            label = "Packages"
        return Text(
            f"[0] {label}",
            style=ACTIVE_SECTION_TITLE_STYLE
            if selected
            else INACTIVE_SECTION_TITLE_STYLE,
        )

    def _download_indicator_text(self) -> Text:
        if self._download_indicator_override is not None:
            return Text(self._download_indicator_override, style="dim")

        indicator = Text("Download: d", style="dim")
        indicator.stylize("bold red", len("Download: "), len("Download: d"))
        return indicator

    def _set_download_indicator(self, value: str | None) -> None:
        self._download_indicator_override = value
        self.query_one("#footer", Static).update(self._footer_text())
        if self._mode == "versions":
            self._update_versions_status()
            return
        self._update_download_indicator()

    def _update_download_indicator(self) -> None:
        main_panel = self.query_one("#main-panel", MainPanel)
        main_panel.styles.border_title_align = "left"
        main_panel.border_title = ""
        main_panel.styles.border_subtitle_align = "right"
        main_panel.border_subtitle = ""

    def _selected_platforms_text(self) -> str:
        return ", ".join(
            str(platform)
            for platform in sorted(
                self._selected_platform_names,
                key=platform_sort_key,
            )
        )

    def _update_platform_selection_status(self) -> None:
        selected = (
            set(self._draft_selected_platform_names)
            if self._draft_selected_platform_names is not None
            else set(self._selected_platform_names)
        )
        all_platforms = set(self._available_platform_names)
        selected_count = len(selected)

        message = Text()
        message.append(
            f"{selected_count} platforms selected.\nSelect/deselect: Space\nApply: Enter\n"
        )
        if selected == all_platforms:
            message.append("All platforms selected")
        else:
            message.append("All platforms: a")

        self.query_one("#status", Static).update(message)

    def _update_filter_indicator(self) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        sidebar_selected = self._selected_pane == "sidebar"
        sidebar.set_class(sidebar_selected, "-active-pane")
        sidebar.border_title = self._sidebar_title_text(selected=sidebar_selected)
        sidebar.border_subtitle = ""
        main_panel = self.query_one("#main-panel", MainPanel)
        main_selected = self._selected_pane == "main"
        main_panel.set_class(main_selected, "-active-pane")
        main_panel.set_pane_selected(main_selected)
        self.query_one("#footer", Static).update(self._footer_text())
        self._update_download_indicator()

    def _update_platform_indicator(self) -> None:
        self._update_filter_indicator()

    def _set_filter_mode(self, enabled: bool, *, reset_query: bool) -> None:
        self._filter_mode = enabled
        if reset_query:
            self._search_query = ""
        if self._mode == "packages":
            self._filter_packages()
        self._update_filter_indicator()

    def _append_filter_char(self, char: str) -> None:
        self._search_query += char
        self._filter_packages()
        self._update_filter_indicator()

    def _set_channel_edit_mode(self, enabled: bool, *, reset_draft: bool) -> None:
        self._channel_edit_mode = enabled
        if reset_draft:
            self._channel_draft = self._channel_name
        self._update_filter_indicator()

    def _append_channel_char(self, char: str) -> None:
        self._channel_draft += char
        self._update_filter_indicator()

    def _confirm_channel_edit(self) -> None:
        channel_name = self._channel_draft.strip()
        if not channel_name:
            self.notify(
                "Channel cannot be empty.",
                title="Channel",
                severity="warning",
            )
            return
        self._set_channel_edit_mode(False, reset_draft=False)
        self.run_worker(
            self._apply_channel_selection(channel_name),
            group="channel-selection",
            exclusive=True,
            exit_on_error=False,
        )

    def action_filter_key_slash(self) -> None:
        if self._channel_edit_mode:
            self._append_channel_char("/")
            return

        if self._mode != "packages":
            return

        if not self._filter_mode:
            self._set_filter_mode(True, reset_query=True)
            return

        self._append_filter_char("/")

    def action_platform_key_p(self) -> None:
        if self._channel_edit_mode:
            self._append_channel_char("p")
            return

        if self._mode == "packages" and self._filter_mode:
            self._append_filter_char("p")
            return
        self._open_platform_selector()

    def action_channel_key_c(self) -> None:
        if self._channel_edit_mode:
            self._append_channel_char("c")
            return

        if self._mode == "packages" and self._filter_mode:
            self._append_filter_char("c")
            return
        self._set_channel_edit_mode(True, reset_draft=True)
        self._update_filter_indicator()

    def action_compare_key_c(self) -> None:
        if self._channel_edit_mode:
            self._append_channel_char("C")
            return

        if self._mode == "packages" and self._filter_mode:
            self._append_filter_char("C")
            return

        if self._mode != "versions" or self._compare_screen_open:
            return

        selection = self._current_compare_selection()
        if selection is None:
            return

        if self._compare_selection is None:
            self._compare_selection = selection
            self.notify(
                f"Stored {self._compare_selection_label(selection)} as compare A.",
                title="Compare",
            )
            return

        if self._compare_selection_key(
            self._compare_selection
        ) == self._compare_selection_key(selection):
            self.notify(
                "Select a different artifact for compare B.",
                title="Compare",
                severity="warning",
            )
            return

        self.run_worker(
            self._open_compare_screen(self._compare_selection, selection),
            group="version-compare",
            exclusive=True,
            exit_on_error=False,
        )

    def _handle_matchspec_result(self, result: MatchSpec | Empty | None) -> None:
        if result is None:
            return

        if isinstance(result, Empty):
            matchspec: MatchSpec | None = None
        else:
            assert isinstance(result, MatchSpec)
            matchspec = result
        self.run_worker(
            self._apply_matchspec_query(matchspec),
            group="matchspec-selection",
            exclusive=True,
            exit_on_error=False,
        )

    def action_matchspec_key_m(self) -> None:
        if self._channel_edit_mode or self._filter_mode:
            return

        self._open_matchspec_screen(self._matchspec_query)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen(self._help_text(), version=__version__))

    def action_open_external_url(self, url: str) -> None:
        webbrowser.open(url)

    def action_select_dependency_tab(self, tab: str) -> None:
        if self._mode != "versions":
            return
        if tab not in DEPENDENCY_TABS:
            return
        self._set_active_main_section(1)
        self._set_main_dependency_tab(cast(DependencyTab, tab))
        self._focus_main_panel()

    def action_tab_key(self) -> None:
        if self._compare_screen_open and isinstance(self.screen, CompareScreen):
            compare_screen = cast(CompareScreen, self.screen)
            compare_screen.action_next_section()
            return
        if self._mode != "versions":
            return
        if not self._main_panel_shows_version_details():
            return
        if not self._main_panel_is_focused():
            return
        self._cycle_active_main_section(1)

    def action_backtab_key(self) -> None:
        if self._compare_screen_open and isinstance(self.screen, CompareScreen):
            compare_screen = cast(CompareScreen, self.screen)
            compare_screen.action_previous_section()
            return
        if self._mode != "versions":
            return
        if not self._main_panel_shows_version_details():
            return
        if not self._main_panel_is_focused():
            return
        self._cycle_active_main_section(-1)

    def action_quit_or_type_q(self) -> None:
        if self._channel_edit_mode:
            self._append_channel_char("q")
            return

        if self._mode == "packages" and self._filter_mode:
            self._append_filter_char("q")
            return
        self.exit()

    def action_escape(self) -> None:
        if self._channel_edit_mode:
            self._set_channel_edit_mode(False, reset_draft=True)
            return

        if self._main_panel_is_focused():
            self._focus_sidebar()
            return

        if self._mode in {"versions", "platforms"}:
            self._back_to_packages()
            return

        if self._filter_mode:
            self._set_filter_mode(False, reset_query=True)

    def on_key(self, event: Key) -> None:
        if self._compare_screen_open and isinstance(self.screen, CompareScreen):
            compare_screen = cast(CompareScreen, self.screen)
            if event.key == "tab":
                compare_screen.action_next_section()
                event.stop()
                return
            if event.key in {"shift+tab", "backtab"}:
                compare_screen.action_previous_section()
                event.stop()
                return

        if self._channel_edit_mode:
            self._reset_sidebar_vim_pending()
            if event.key in {"p", "c", "slash", "q"}:
                return

            if event.key == "enter":
                self._confirm_channel_edit()
                event.stop()
                return

            if event.key == "backspace":
                self._channel_draft = self._channel_draft[:-1]
                self._update_filter_indicator()
                event.stop()
                return

            if event.key == "space":
                self._append_channel_char(" ")
                event.stop()
                return

            if event.character and event.character.isprintable():
                self._append_channel_char(event.character)
                event.stop()
                return

            return

        if self._sidebar_is_focused() and not (
            self._mode == "packages" and self._filter_mode
        ):
            if event.key == "enter":
                self._reset_sidebar_vim_pending()
                self._sidebar_selection_by_keyboard = True
                return
            if event.character == "j":
                self._reset_sidebar_vim_pending()
                self._move_sidebar_highlight(1)
                event.stop()
                return
            if event.character == "k":
                self._reset_sidebar_vim_pending()
                self._move_sidebar_highlight(-1)
                event.stop()
                return
            if event.character == "l":
                self._reset_sidebar_vim_pending()
                self._focus_main_panel()
                event.stop()
                return
            if event.key == "ctrl+d":
                self._reset_sidebar_vim_pending()
                self._page_sidebar(1)
                event.stop()
                return
            if event.key == "ctrl+u":
                self._reset_sidebar_vim_pending()
                self._page_sidebar(-1)
                event.stop()
                return
            if event.character == "g":
                if self._sidebar_vim_g_pending:
                    self._jump_sidebar_first()
                    self._reset_sidebar_vim_pending()
                else:
                    self._sidebar_vim_g_pending = True
                event.stop()
                return
            if event.character == "G":
                self._reset_sidebar_vim_pending()
                self._jump_sidebar_last()
                event.stop()
                return

        self._reset_sidebar_vim_pending()

        if (
            self._mode == "versions"
            and self._main_panel_shows_version_details()
            and self._main_panel_is_focused()
            and event.key == "enter"
        ):
            main_panel = self.query_one("#main-panel", MainPanel)
            if main_panel.dependency_section_is_active():
                matchspec = self._selected_dependency_matchspec()
                if matchspec is not None:
                    self._defer_matchspec_screen(matchspec)
                event.stop()
                return
            if main_panel.file_section_is_active():
                self._request_file_action_for_selected_file()
                event.stop()
                return
            event.stop()
            return

        if (
            self._mode == "versions"
            and self._main_panel_shows_version_details()
            and self._main_panel_is_focused()
            and event.key == "tab"
        ):
            self._cycle_active_main_section(1)
            event.stop()
            return

        if (
            self._mode == "versions"
            and self._main_panel_shows_version_details()
            and self._main_panel_is_focused()
            and event.key in {"shift+tab", "backtab"}
        ):
            self._cycle_active_main_section(-1)
            event.stop()
            return

        if (
            self._mode == "versions"
            and (
                not self._main_panel_shows_version_details()
                or not self._main_panel_is_focused()
            )
            and event.key in {"tab", "shift+tab", "backtab"}
        ):
            event.stop()
            return

        if (
            self._mode == "versions"
            and self._main_panel_shows_version_details()
            and event.character in {"1", "2", "3"}
        ):
            self._set_active_main_section(int(event.character) - 1)
            self._focus_main_panel()
            event.stop()
            return

        if event.character == "0" and self._mode in {"packages", "versions"}:
            self._focus_sidebar()
            event.stop()
            return

        if event.character == "1" and self._mode in {"packages", "versions"}:
            self._focus_main_panel()
            event.stop()
            return

        if (
            self._mode == "versions"
            and event.character == "["
            and self._selected_pane == "main"
            and self.query_one("#main-panel", MainPanel).dependency_section_is_active()
        ):
            self._cycle_main_dependency_tab(-1)
            self._focus_main_panel()
            event.stop()
            return

        if (
            self._mode == "versions"
            and event.character == "]"
            and self._selected_pane == "main"
            and self.query_one("#main-panel", MainPanel).dependency_section_is_active()
        ):
            self._cycle_main_dependency_tab(1)
            self._focus_main_panel()
            event.stop()
            return

        if self._mode == "platforms" and event.key == "a":
            if not self._available_platform_names:
                return
            all_platforms = set(self._available_platform_names)
            draft = self._draft_selected_platform_names
            if draft is None:
                draft = set(self._selected_platform_names)

            if draft != all_platforms:
                self._draft_selected_platform_names = set(all_platforms)
                package_list = self.query_one("#sidebar-list", OptionList)
                highlighted = package_list.highlighted
                self._render_platform_options()
                if highlighted is not None:
                    self.query_one("#sidebar-list", OptionList).highlighted = min(
                        highlighted, len(self._available_platform_names) - 1
                    )
                self._update_platform_indicator()
                self._update_platform_selection_status()
            event.stop()
            return

        if self._mode == "platforms" and event.key == "space":
            package_list = self.query_one("#sidebar-list", OptionList)
            highlighted = package_list.highlighted
            if highlighted is None:
                return
            self._toggle_platform_at_index(highlighted)
            event.stop()
            return

        if self._mode == "versions" and event.key == "d":
            self._request_download_for_highlighted_entry()
            event.stop()
            return

        if not self._filter_mode or self._mode != "packages":
            return

        if event.key in {"p", "c", "slash", "q"}:
            return

        if event.key == "backspace":
            self._search_query = self._search_query[:-1]
            self._filter_packages()
            self._update_filter_indicator()
            event.stop()
            return

        if event.key == "space":
            self._search_query += " "
            self._filter_packages()
            self._update_filter_indicator()
            event.stop()
            return

        if event.character and event.character.isprintable():
            self._search_query += event.character
            self._filter_packages()
            self._update_filter_indicator()
            event.stop()

    def on_paste(self, event: Paste) -> None:
        if not self._channel_edit_mode:
            return
        sanitized = event.text.replace("\r", "").replace("\n", "")
        if not sanitized:
            return
        self._append_channel_char(sanitized)
        event.stop()

    def on_resize(self, event: Resize) -> None:
        del event
        self._update_filter_indicator()
        self.call_after_refresh(self._refresh_after_resize)

    def _refresh_after_resize(self) -> None:
        self._update_filter_indicator()
        package_list = self.query_one("#sidebar-list", OptionList)
        if self._mode == "packages":
            self._render_package_options(preserve_position=True)
        elif self._mode == "versions":
            self._render_version_options(preserve_position=True)
            self._update_versions_status()
            self._rerender_visible_version_preview()
        elif self._mode == "platforms":
            highlighted = package_list.highlighted
            previous_scroll_y = package_list.scroll_y
            self._render_platform_options()
            if highlighted is not None and self._available_platform_names:
                package_list.highlighted = min(
                    highlighted, len(self._available_platform_names) - 1
                )
                package_list.scroll_to(y=previous_scroll_y, animate=False)
            self._update_platform_selection_status()

    def _highlighted_version_row(self) -> VersionRow | None:
        if self._mode != "versions":
            return None

        package_list = self.query_one("#sidebar-list", OptionList)
        highlighted = package_list.highlighted
        if (
            highlighted is None
            or highlighted < 0
            or highlighted >= len(self._version_rows)
        ):
            return None

        return self._version_rows[highlighted]

    def _rerender_visible_version_preview(self) -> None:
        if self._mode != "versions":
            return
        package_name = self._selected_package
        if package_name is None:
            return

        row = self._highlighted_version_row()
        if row is None or row.kind != "entry" or row.entry is None:
            return

        preview_key = self._version_preview_key(package_name, row.entry)
        cached = self._version_details_cache.get(preview_key)
        if cached is not None:
            self._show_version_details(cached)
            self._previewed_version_key = preview_key
            self._pending_preview_version_key = preview_key
            return

        self._previewed_version_key = None
        self._pending_preview_version_key = None
        self._request_selected_version_preview(package_name, row.entry)

    def _update_main_panel_for_sidebar_highlight(self, option_index: int) -> None:
        if self._mode == "packages":
            if option_index < 0 or option_index >= len(self._visible_package_names):
                return
            self._request_package_preview(self._visible_package_names[option_index])
            return

        if self._mode != "versions":
            return
        if option_index < 0 or option_index >= len(self._version_rows):
            return

        row = self._version_rows[option_index]
        package_name = self._selected_package
        if package_name is None:
            return

        if row.kind == "entry" and row.entry is not None:
            self._request_selected_version_preview(package_name, row.entry)
            return
        if row.kind == "section" and row.subdir is not None:
            self._previewed_version_key = None
            self._pending_preview_version_key = None
            self._show_main_placeholder(
                f"# {escape(package_name)}\n\n"
                f"Platform section: {escape(row.subdir)}\n"
                "Press Enter to collapse or expand."
            )
            self._reset_main_panel_scroll()
            return
        if row.kind == "back":
            self._previewed_version_key = None
            self._pending_preview_version_key = None
            matchspec_records = self._matchspec_records_by_package.get(package_name)
            if matchspec_records is not None:
                self._update_main_panel_for_package(package_name, matchspec_records)
                return
            cached = self._package_records_cache.get(package_name)
            if cached is not None:
                self._update_main_panel_for_package(package_name, cached)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id != "sidebar-list":
            return
        if self._sidebar_is_focused() and self._selected_pane != "sidebar":
            self._set_selected_pane("sidebar")
        self._update_main_panel_for_sidebar_highlight(event.option_index)

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        selection_by_keyboard = self._sidebar_selection_by_keyboard
        self._sidebar_selection_by_keyboard = False

        if event.option_list.id == "detail-option-list-1":
            matchspec = self._dependency_matchspec_at(event.option_index)
            if matchspec is None:
                return
            self._set_active_main_section(1)
            self._focus_main_panel()
            self._defer_matchspec_screen(matchspec)
            return

        if event.option_list.id == "detail-option-list-2":
            self._set_active_main_section(2)
            self._focus_main_panel()
            self._request_file_action_for_selected_file()
            return

        if event.option_list.id != "sidebar-list":
            return

        if self._mode == "packages":
            if not self._visible_package_names:
                return
            if event.option_index < 0 or event.option_index >= len(
                self._visible_package_names
            ):
                return
            selected = self._visible_package_names[event.option_index]
            await self._open_versions(selected)
            return

        if self._mode == "platforms":
            await self._apply_platform_selection()
            return

        if self._mode != "versions":
            return

        if event.option_index < 0 or event.option_index >= len(self._version_rows):
            return

        row = self._version_rows[event.option_index]
        if row.kind == "back":
            self._back_to_packages()
            return

        if row.kind == "section":
            assert row.subdir is not None
            self._toggle_version_section(row.subdir)
            self._update_versions_status()
            return

        if row.kind != "entry" or row.entry is None:
            return

        version = row.entry
        package_name = self._selected_package or "<unknown>"
        self._request_selected_version_preview(package_name, version)
        if selection_by_keyboard:
            self._focus_main_panel()
