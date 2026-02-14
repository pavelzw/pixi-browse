from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from rattler.exceptions import GatewayError
from rattler.platform import Platform
from rattler.repo_data import Gateway
from rattler.version import Version
from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key, Paste, Resize
from textual.widgets import OptionList, Static

from pixi_browse.downloads import download_url_to_path
from pixi_browse.models import VersionEntry, VersionPreviewKey, VersionRow, ViewMode
from pixi_browse.platform_utils import platform_sort_key
from pixi_browse.rendering import (
    format_byte_size,
    format_detail_row,
    format_record_value,
    render_kv_box,
    render_package_preview,
    render_selected_version_details,
)
from pixi_browse.repodata import (
    create_gateway,
    discover_available_platforms,
    fetch_package_names,
    query_package_records,
)
from pixi_browse.search import fuzzy_score


class CondaMetadataTui(App[None]):
    CSS_PATH = "selection_list.tcss"
    ENABLE_COMMAND_PALETTE = False
    DOWNLOAD_TIMEOUT_SECONDS = 60.0
    BINDINGS = [
        Binding("f", "filter_key_f", "Filter"),
        Binding("p", "platform_key_p", "Platform"),
        Binding("c", "channel_key_c", "Channel"),
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
    ) -> None:
        super().__init__()
        channel_name = default_channel.strip() or "conda-forge"
        selected_platforms = set(default_platforms or [])
        self.theme = "rose-pine"
        self._gateway: Gateway = create_gateway(cache_dir=self._gateway_cache_path())
        self._platforms: list[Platform] = []
        self._available_platform_names: list[Platform] = []
        self._selected_platform_names: set[Platform] = set(selected_platforms)
        self._draft_selected_platform_names: set[Platform] | None = None
        self._package_records_cache: dict[str, list[Any]] = {}
        self._channel_name = channel_name
        self._mode: ViewMode = "packages"
        self._search_query = ""
        self._all_package_names: list[str] = []
        self._visible_package_names: list[str] = []
        self._current_versions: list[VersionEntry] = []
        self._version_subdirs: list[str] = []
        self._versions_by_subdir: dict[str, list[VersionEntry]] = {}
        self._collapsed_version_subdirs: set[str] = set()
        self._version_rows: list[VersionRow] = []
        self._version_details_cache: dict[VersionPreviewKey, str] = {}
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
        self._last_package_highlight: int | None = None
        self._last_package_scroll_y = 0.0

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("Packages", id="sidebar-title")
                yield OptionList(id="sidebar-list")
                yield Static("Loading repodata...", id="status")
            with Vertical(id="main-panel"):
                yield Static(
                    "Main panel placeholder.\n\nSelect a package in the sidebar.",
                    id="main-placeholder",
                )

    async def on_mount(self) -> None:
        package_list = self.query_one("#sidebar-list", OptionList)
        package_list.disabled = True
        package_list.focus()
        self._update_filter_indicator()
        await self._load_packages()

    async def _load_packages(self) -> bool:
        status = self.query_one("#status", Static)
        status.update("Discovering available platforms via sharded gateway...")
        try:
            await self._ensure_available_platforms()
            status.update(
                f"Downloading repodata for {self._selected_platforms_text()} (sharded)..."
            )
            self._all_package_names = await self._fetch_package_names_with_gateway()
        except (GatewayError, RuntimeError) as exc:
            status.update(f"Failed to load repodata: {exc!s}")
            return False

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

    def _gateway_cache_path(self) -> Path:
        # todo: use rattler default
        cache_path = Path.home() / ".cache" / "pixi-browse" / "repodata-gateway"
        cache_path.mkdir(parents=True, exist_ok=True)
        return cache_path

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

        self._selected_platform_names = self._default_platform_selection()

    def _default_platform_selection(self) -> set[Platform]:
        current_platform = Platform.current()
        noarch_platform = Platform("noarch")
        if current_platform in self._available_platform_names:
            defaults = {current_platform}
            if noarch_platform in self._available_platform_names:
                defaults.add(noarch_platform)
            return defaults

        if noarch_platform in self._available_platform_names:
            return {noarch_platform}

        # Defensive fallback in case noarch is unexpectedly missing.
        return {self._available_platform_names[0]}

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
            f"{len(self._version_subdirs)} platform(s). Enter toggles section."
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

    def _open_platform_selector(self) -> None:
        if self._mode != "packages":
            return

        package_list = self.query_one("#sidebar-list", OptionList)
        self._last_package_highlight = package_list.highlighted
        self._last_package_scroll_y = package_list.scroll_y

        self._mode = "platforms"
        self._draft_selected_platform_names = set(self._selected_platform_names)
        self.query_one("#sidebar-title", Static).update("Platforms")
        self._render_platform_options()
        self._update_platform_selection_status()
        self._update_platform_indicator()

    def _clear_record_caches(self) -> None:
        self._package_records_cache.clear()
        self._version_details_cache.clear()

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
        self._platforms = []
        self._available_platform_names = []
        self._selected_platform_names = set()
        self._all_package_names = []
        self._visible_package_names = []
        self._clear_record_caches()

    def _snapshot_channel_state(self) -> dict[str, Any]:
        return {
            "channel_name": self._channel_name,
            "mode": self._mode,
            "draft_selected_platform_names": (
                set(self._draft_selected_platform_names)
                if self._draft_selected_platform_names is not None
                else None
            ),
            "current_versions": list(self._current_versions),
            "version_subdirs": list(self._version_subdirs),
            "versions_by_subdir": {
                subdir: list(entries)
                for subdir, entries in self._versions_by_subdir.items()
            },
            "collapsed_version_subdirs": set(self._collapsed_version_subdirs),
            "version_rows": list(self._version_rows),
            "selected_package": self._selected_package,
            "previewed_version_key": self._previewed_version_key,
            "pending_preview_version_key": self._pending_preview_version_key,
            "previewed_package": self._previewed_package,
            "pending_preview_package": self._pending_preview_package,
            "platforms": list(self._platforms),
            "available_platform_names": list(self._available_platform_names),
            "selected_platform_names": set(self._selected_platform_names),
            "all_package_names": list(self._all_package_names),
            "visible_package_names": list(self._visible_package_names),
            "package_records_cache": {
                package_name: list(records)
                for package_name, records in self._package_records_cache.items()
            },
            "version_details_cache": dict(self._version_details_cache),
            "last_package_highlight": self._last_package_highlight,
            "last_package_scroll_y": self._last_package_scroll_y,
        }

    def _restore_channel_state(self, snapshot: dict[str, Any]) -> None:
        self._channel_name = snapshot["channel_name"]
        self._mode = snapshot["mode"]
        self._draft_selected_platform_names = snapshot["draft_selected_platform_names"]
        self._current_versions = snapshot["current_versions"]
        self._version_subdirs = snapshot["version_subdirs"]
        self._versions_by_subdir = snapshot["versions_by_subdir"]
        self._collapsed_version_subdirs = snapshot["collapsed_version_subdirs"]
        self._version_rows = snapshot["version_rows"]
        self._selected_package = snapshot["selected_package"]
        self._previewed_version_key = snapshot["previewed_version_key"]
        self._pending_preview_version_key = snapshot["pending_preview_version_key"]
        self._previewed_package = snapshot["previewed_package"]
        self._pending_preview_package = snapshot["pending_preview_package"]
        self._platforms = snapshot["platforms"]
        self._available_platform_names = snapshot["available_platform_names"]
        self._selected_platform_names = snapshot["selected_platform_names"]
        self._all_package_names = snapshot["all_package_names"]
        self._visible_package_names = snapshot["visible_package_names"]
        self._package_records_cache = snapshot["package_records_cache"]
        self._version_details_cache = snapshot["version_details_cache"]
        self._last_package_highlight = snapshot["last_package_highlight"]
        self._last_package_scroll_y = snapshot["last_package_scroll_y"]

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

        previous_platforms = set(self._selected_platform_names)
        self._selected_platform_names = set(selected)
        self._draft_selected_platform_names = None
        self._update_platform_indicator()
        self.query_one("#status", Static).update(
            f"Loading repodata for {self._selected_platforms_text()}..."
        )

        self._clear_record_caches()
        self._reset_preview_state()

        try:
            self._all_package_names = await self._fetch_package_names_with_gateway()
        except (GatewayError, RuntimeError) as exc:
            self._selected_platform_names = previous_platforms
            self._update_platform_indicator()
            self.query_one("#status", Static).update(
                f"Failed to load selected platforms: {exc!s}"
            )
            self._back_to_packages()
            return

        self._mode = "packages"
        self.query_one("#sidebar-title", Static).update("Packages")
        self._filter_packages()
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
        package_list.disabled = True
        self.query_one("#sidebar-title", Static).update("Packages")
        self.query_one("#main-placeholder", Static).update(
            f"# {escape(channel_name)}\n\nLoading repodata..."
        )
        self._update_filter_indicator()

        loaded = await self._load_packages()
        if not loaded:
            self._restore_channel_state(previous_state)
            self._back_to_packages()
            package_list.disabled = False
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

    def _record_sort_key(self, record: Any) -> tuple[Any, str, str, int]:
        return (record.version, record.build, record.subdir, record.build_number)

    async def _get_package_records(self, package_name: str) -> list[Any]:
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

    def _format_detail_row(self, label: str, value: str) -> str:
        return format_detail_row(label, value)

    def _main_panel_content_width(self) -> int:
        main_panel = self.query_one("#main-panel", Vertical)
        if main_panel.size.width <= 0:
            return 90
        return max(50, main_panel.size.width - 6)

    def _format_record_value(self, value: Any) -> str:
        return format_record_value(value)

    def _format_byte_size(self, value: Any) -> str:
        return format_byte_size(value)

    def _render_kv_box(self, rows: list[tuple[str, str]], width: int) -> list[str]:
        return render_kv_box(rows, width)

    async def _get_record_for_version_entry(
        self, package_name: str, entry: VersionEntry
    ) -> Any | None:
        for record in await self._get_package_records(package_name):
            if (
                record.version == entry.version
                and record.build == entry.build
                and record.build_number == entry.build_number
                and record.subdir == entry.subdir
                and record.file_name == entry.file_name
            ):
                return record
        return None

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

    def _render_selected_version_details(self, package_name: str, record: Any) -> str:
        return render_selected_version_details(
            package_name,
            record,
            content_width=self._main_panel_content_width(),
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
            rendered = "No matching repodata record found for selected version."
        else:
            rendered = self._render_selected_version_details(package_name, record)

        self._version_details_cache[preview_key] = rendered
        self.query_one("#main-placeholder", Static).update(rendered)
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
            self.query_one("#main-placeholder", Static).update(cached)
            self._previewed_version_key = preview_key
            return

        self.query_one("#main-placeholder", Static).update(
            f"# {escape(package_name)} {escape(str(entry.version))}\n\n"
            "Loading repodata for selected version..."
        )
        self.run_worker(
            self._load_and_render_selected_version_preview(
                package_name, entry, preview_key
            ),
            group="version-preview",
            exclusive=True,
            exit_on_error=False,
        )

    @staticmethod
    def _download_url_to_path(
        url: str, destination: Path, *, timeout_seconds: float
    ) -> None:
        download_url_to_path(url, destination, timeout_seconds=timeout_seconds)

    async def _download_selected_version_entry(
        self, package_name: str, entry: VersionEntry
    ) -> None:
        self._download_in_progress = True
        self._set_download_indicator(f"Downloading {entry.file_name}...")

        temporary_destination: Path | None = None
        try:
            record = await self._get_record_for_version_entry(package_name, entry)
            if record is not None:
                url = str(record.url)
            else:
                url = (
                    f"https://conda.anaconda.org/{self._channel_name}/"
                    f"{entry.subdir}/{entry.file_name}"
                )

            destination = (Path.cwd() / entry.file_name).resolve()
            temporary_destination = destination.with_name(f"{destination.name}.part")
            await asyncio.to_thread(
                self._download_url_to_path,
                url,
                temporary_destination,
                timeout_seconds=self.DOWNLOAD_TIMEOUT_SECONDS,
            )
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

    def _render_package_preview(self, package_name: str, records: list[Any]) -> str:
        return render_package_preview(
            package_name,
            records,
            record_sort_key=self._record_sort_key,
        )

    def _update_main_panel_for_package(
        self, package_name: str, records: list[Any]
    ) -> None:
        self.query_one("#main-placeholder", Static).update(
            self._render_package_preview(package_name, records)
        )
        self._previewed_package = package_name

    async def _load_and_render_package_preview(self, package_name: str) -> None:
        records = await self._get_package_records(package_name)
        if self._mode != "packages":
            return
        if self._pending_preview_package != package_name:
            return
        self._update_main_panel_for_package(package_name, records)

    def _request_package_preview(self, package_name: str) -> None:
        self._pending_preview_package = package_name
        if self._previewed_package == package_name:
            return
        cached = self._package_records_cache.get(package_name)
        if cached is not None:
            self._update_main_panel_for_package(package_name, cached)
            return

        self.query_one("#main-placeholder", Static).update(
            f"# {package_name}\n\nLoading repodata..."
        )
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
            self.query_one("#main-placeholder", Static).update(
                "No packages match the current selection."
            )

    def _back_to_packages(self) -> None:
        self._mode = "packages"
        self._draft_selected_platform_names = None
        self._clear_version_state()
        self._previewed_version_key = None
        self._pending_preview_version_key = None
        self.query_one("#sidebar-title", Static).update("Packages")
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

    async def _open_versions(self, package_name: str) -> None:
        package_list = self.query_one("#sidebar-list", OptionList)
        self._last_package_highlight = package_list.highlighted
        self._last_package_scroll_y = package_list.scroll_y

        self._selected_package = package_name
        self._current_versions = self._build_version_entries(
            await self._get_package_records(package_name)
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
        self.query_one("#sidebar-title", Static).update(f"Versions: {package_name}")
        self._update_filter_indicator()
        self._render_version_options()
        self._update_versions_status()
        self._previewed_version_key = None
        self._pending_preview_version_key = None
        self._pending_preview_package = None
        self._previewed_package = package_name

    def _build_version_entries(self, records: list[Any]) -> list[VersionEntry]:
        """Build version entries while preserving distinct artifacts per build."""
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

    def _filter_indicator_text(self) -> Text:
        indicator = Text()
        if self._filter_mode:
            indicator.append("f", style="bold red")
            indicator.append(f" {self._search_query}_", style="bold white")
        else:
            indicator.append("filter", style="dim")
            indicator.stylize("bold red", 0, 1)
        return indicator

    def _platform_indicator_text(self) -> Text:
        indicator = Text("platform", style="dim")
        indicator.stylize("bold red", 0, 1)
        platforms = (
            sorted(
                self._draft_selected_platform_names,
                key=platform_sort_key,
            )
            if self._mode == "platforms"
            and self._draft_selected_platform_names is not None
            else sorted(self._selected_platform_names, key=platform_sort_key)
        )
        platform_names = [str(platform) for platform in platforms]
        if platform_names:
            if len(platform_names) <= 2:
                summary = "+".join(platform_names)
            else:
                summary = f"{platform_names[0]}+{len(platform_names) - 1}"
            indicator.append(f" {summary}", style="bold white")
        return indicator

    def _channel_indicator_text(self) -> Text:
        indicator = Text("")
        indicator.append("c", style="bold red")
        indicator.append("hannel", style="dim")
        if self._channel_edit_mode:
            indicator.append(f" {self._channel_draft}_", style="bold white")
        else:
            indicator.append(f" {self._channel_name}", style="bold white")
        return indicator

    def _download_indicator_text(self) -> Text:
        if self._download_indicator_override is not None:
            return Text(self._download_indicator_override)

        indicator = Text("download", style="dim")
        indicator.stylize("bold red", 0, 1)
        return indicator

    def _set_download_indicator(self, value: str | None) -> None:
        self._download_indicator_override = value
        self._update_download_indicator()

    def _update_download_indicator(self) -> None:
        main_panel = self.query_one("#main-panel", Vertical)
        main_panel.styles.border_title_align = "left"
        main_panel.border_title = self._channel_indicator_text()
        main_panel.styles.border_subtitle_align = "left"
        if self._mode == "versions":
            main_panel.border_subtitle = self._download_indicator_text()
        else:
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
        message.append(f"{selected_count} platforms selected. Press Enter to apply.\n")
        if selected == all_platforms:
            message.append("Select def")
            message.append("a", style="bold red")
            message.append("ult platforms")
        else:
            message.append("Select ")
            message.append("a", style="bold red")
            message.append("ll platforms")

        self.query_one("#status", Static).update(message)

    def _update_filter_indicator(self) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        filter_indicator = self._filter_indicator_text()
        platform_indicator = self._platform_indicator_text()
        right_indicator = platform_indicator

        spacing = 1
        sidebar_width = sidebar.size.width
        if sidebar_width > 0:
            title_width = max(1, sidebar_width - 2)
            spacing = max(
                1,
                title_width - len(filter_indicator.plain) - len(right_indicator.plain),
            )

        sidebar.border_title = Text.assemble(
            filter_indicator, " " * spacing, right_indicator
        )
        sidebar.border_subtitle = ""
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

    def action_filter_key_f(self) -> None:
        if self._channel_edit_mode:
            self._append_channel_char("f")
            return

        if self._mode != "packages":
            return

        if not self._filter_mode:
            self._set_filter_mode(True, reset_query=True)
            return

        self._append_filter_char("f")

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
        self._set_channel_edit_mode(True, reset_draft=False)
        self._channel_draft = ""
        self._update_filter_indicator()

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

        if self._mode in {"versions", "platforms"}:
            self._back_to_packages()
            self.query_one("#sidebar-list", OptionList).focus()
            return

        if self._filter_mode:
            self._set_filter_mode(False, reset_query=True)

    def on_key(self, event: Key) -> None:
        if self._channel_edit_mode:
            # These keys are handled by explicit bindings to avoid duplicate input.
            if event.key in {"f", "p", "c", "slash", "q"}:
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

        if self._mode == "platforms" and event.key == "a":
            if not self._available_platform_names:
                return
            all_platforms = set(self._available_platform_names)
            defaults = self._default_platform_selection()
            draft = self._draft_selected_platform_names
            if draft is None:
                draft = set(self._selected_platform_names)

            if draft == all_platforms:
                self._draft_selected_platform_names = set(defaults)
            else:
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

        # These keys are handled by explicit bindings to avoid duplicate input.
        if event.key in {"f", "p", "c", "slash", "q"}:
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
            return

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
        if row is None:
            return
        if row.kind != "entry" or row.entry is None:
            return

        # Version details include width-dependent formatting, so invalidate cache on resize.
        self._version_details_cache.clear()
        self._previewed_version_key = None
        self._pending_preview_version_key = None
        self._request_selected_version_preview(package_name, row.entry)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id != "sidebar-list":
            return
        if self._mode == "packages":
            if not self._visible_package_names:
                return
            if event.option_index < 0 or event.option_index >= len(
                self._visible_package_names
            ):
                return
            self._request_package_preview(
                self._visible_package_names[event.option_index]
            )
            return

        if self._mode != "versions":
            return
        if event.option_index < 0 or event.option_index >= len(self._version_rows):
            return

        row = self._version_rows[event.option_index]
        package_name = self._selected_package
        if package_name is None:
            return

        if row.kind == "entry" and row.entry is not None:
            self._request_selected_version_preview(package_name, row.entry)
            return
        if row.kind == "section" and row.subdir is not None:
            self.query_one("#main-placeholder", Static).update(
                f"# {escape(package_name)}\n\n"
                f"Platform section: {escape(row.subdir)}\n"
                "Press Enter to collapse or expand."
            )
            return
        if row.kind == "back":
            cached = self._package_records_cache.get(package_name)
            if cached is not None:
                self._update_main_panel_for_package(package_name, cached)

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
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
