from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast

from rattler.exceptions import InvalidMatchSpecError
from rattler.match_spec import MatchSpec
from rich import box
from rich.console import RenderableType
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.events import Click, Key
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, OptionList, Static

from pixi_browse.models import (
    CompareRow,
    CompareSelection,
    DependencyTab,
    VersionCompareData,
    VersionDetailsData,
)
from pixi_browse.rendering import format_human_byte_size

DEPENDENCY_TABS: tuple[DependencyTab, ...] = (
    "dependencies",
    "constraints",
    "run_exports",
)
ACTIVE_SECTION_TITLE_STYLE = Style(color="#ec4899", bold=True)
INACTIVE_SECTION_TITLE_STYLE = Style(color="white", bold=False)
ACTIVE_TAB_STYLE = Style(color="#ec4899", bold=True)
INACTIVE_SELECTED_TAB_STYLE = Style(color="#ec4899", bold=False)
INACTIVE_TAB_STYLE = INACTIVE_SECTION_TITLE_STYLE
DETAIL_SELECT_DEPENDENCY_TAB_ACTION = "select_dependency_tab"


@dataclass(frozen=True)
class Empty:
    pass


@dataclass(frozen=True)
class DependencyListEntry:
    label: str
    matchspec: str | None


@dataclass(frozen=True)
class FileListEntry:
    label: str
    path: str | None
    size_in_bytes: int | None = None


EMPTY_MATCHSPEC_RESULT = Empty()


FileAction = Literal["download", "preview"]


class DetailOptionList(OptionList):
    can_focus = False


class DetailSection(Vertical):
    def __init__(
        self,
        title: str,
        index: int,
        *,
        on_activate: Callable[[int], None],
        on_select_dependency_tab: Callable[[DependencyTab], None] | None = None,
        show_tabs: bool = False,
        use_option_list: bool = False,
        id_prefix: str = "detail",
    ) -> None:
        super().__init__(classes="detail-section")
        self._index = index
        self._use_option_list = use_option_list
        self._id_prefix = id_prefix
        self._on_activate = on_activate
        self._on_select_dependency_tab = on_select_dependency_tab
        del title, show_tabs
        self.auto_links = False
        self.styles.border_title_align = "left"

    def compose(self) -> ComposeResult:
        if self._use_option_list:
            yield DetailOptionList(
                id=f"{self._id_prefix}-option-list-{self._index}",
                classes="detail-option-list",
                markup=False,
            )
            return

        with VerticalScroll(
            id=f"{self._id_prefix}-scroll-{self._index}", classes="detail-scroll"
        ):
            yield Static(
                id=f"{self._id_prefix}-body-{self._index}",
                classes="detail-body",
            )

    def on_click(self, event: Click) -> None:
        style = event.style
        meta = style.meta if style is not None else None
        click_meta = meta.get("@click") if meta is not None else None
        if click_meta is not None:
            event.stop()
            return
        self._on_activate(self._index)
        event.stop()

    def action_select_dependency_tab(self, tab: DependencyTab) -> None:
        if self._on_select_dependency_tab is None:
            return
        self._on_select_dependency_tab(tab)

    def update_header(self, title: str | Text) -> None:
        self.border_title = title

    def update_body(self, body: RenderableType) -> None:
        self.query_one(f"#{self._id_prefix}-body-{self._index}", Static).update(body)

    def update_options(self, labels: list[str], *, highlighted: int = 0) -> None:
        option_list = self.query_one(
            f"#{self._id_prefix}-option-list-{self._index}", DetailOptionList
        )
        option_list.clear_options()
        option_list.add_options(labels)
        if labels:
            option_list.highlighted = max(0, min(highlighted, len(labels) - 1))

    def set_active(self, active: bool) -> None:
        self.set_class(active, "-active")
        self.set_class(not active, "-collapsed")

    def scroll_body_home(self) -> None:
        self.query_one(
            f"#{self._id_prefix}-scroll-{self._index}", VerticalScroll
        ).scroll_home(animate=False, immediate=True, x_axis=False)

    def scroll_body_end(self) -> None:
        self.query_one(
            f"#{self._id_prefix}-scroll-{self._index}", VerticalScroll
        ).scroll_end(animate=False)

    def scroll_body_by(self, delta: float) -> None:
        scroll = self.query_one(
            f"#{self._id_prefix}-scroll-{self._index}", VerticalScroll
        )
        scroll.scroll_to(y=scroll.scroll_y + delta, animate=False)

    def page_step(self) -> int:
        scroll = self.query_one(
            f"#{self._id_prefix}-scroll-{self._index}", VerticalScroll
        )
        return max(1, scroll.size.height)


class VersionDetailsView(Vertical):
    def __init__(self) -> None:
        super().__init__(id="version-details-view", classes="detail-view")
        self._details: VersionDetailsData | None = None
        self._active_section = 0
        self._dependency_tab_index = 0
        self._dependency_entries: dict[
            DependencyTab, tuple[DependencyListEntry, ...]
        ] = {tab: () for tab in DEPENDENCY_TABS}
        self._file_entries: tuple[FileListEntry, ...] = ()
        self._dependency_highlighted: dict[DependencyTab, int] = {
            tab: 0 for tab in DEPENDENCY_TABS
        }
        self._file_highlighted = 0
        # Duplicate this state so we can avoid updating on every Textual
        # on_focus/on_blur and decide pane selection transitions ourselves.
        self._pane_selected = False

    def compose(self) -> ComposeResult:
        yield DetailSection(
            "Metadata",
            0,
            on_activate=self._activate_section_from_click,
        )
        yield DetailSection(
            "Dependencies",
            1,
            on_activate=self._activate_section_from_click,
            on_select_dependency_tab=self._select_dependency_tab_from_click,
            show_tabs=True,
            use_option_list=True,
        )
        yield DetailSection(
            "Files",
            2,
            on_activate=self._activate_section_from_click,
            use_option_list=True,
        )

    def set_details(self, details: VersionDetailsData) -> None:
        self._details = details
        self._dependency_highlighted = {tab: 0 for tab in DEPENDENCY_TABS}
        self._file_highlighted = 0
        self.display = True
        self._refresh_sections()

    def set_active_section(self, index: int) -> None:
        self._active_section = max(0, min(index, 2))
        self._apply_section_state()

    def set_pane_selected(self, selected: bool) -> None:
        self._pane_selected = selected
        self.set_class(selected, "-pane-selected")
        self._apply_section_state()

    def activate_section(self, index: int, *, focus_main_panel: bool = False) -> None:
        self.set_active_section(index)
        if focus_main_panel:
            self.app.query_one("#main-panel", MainPanel).focus()

    def _activate_section_from_click(self, index: int) -> None:
        self.activate_section(index, focus_main_panel=True)

    def _select_dependency_tab_from_click(self, tab: DependencyTab) -> None:
        self.select_dependency_tab(tab, focus_main_panel=True)

    def cycle_active_section(self, direction: int) -> None:
        self._active_section = (self._active_section + direction) % 3
        self._apply_section_state()

    def cycle_dependency_tab(self, direction: int) -> None:
        self._dependency_tab_index = (self._dependency_tab_index + direction) % len(
            DEPENDENCY_TABS
        )
        self._refresh_dependency_section()

    def set_dependency_tab(self, tab: DependencyTab) -> None:
        self._dependency_tab_index = DEPENDENCY_TABS.index(tab)
        self._refresh_dependency_section()

    def select_dependency_tab(
        self, tab: DependencyTab, *, focus_main_panel: bool = False
    ) -> None:
        self.set_active_section(1)
        self.set_dependency_tab(tab)
        if focus_main_panel:
            self.app.query_one("#main-panel", MainPanel).focus()

    def scroll_home_active(self) -> None:
        if self.dependency_section_is_active():
            self._set_dependency_highlight(0)
            return
        if self.file_section_is_active():
            self._set_file_highlight(0)
            return
        self._section(self._active_section).scroll_body_home()

    def scroll_end_active(self) -> None:
        if self.dependency_section_is_active():
            entries = self._current_dependency_entries()
            if entries:
                self._set_dependency_highlight(len(entries) - 1)
            return
        if self.file_section_is_active():
            file_entries = self._current_file_entries()
            if file_entries:
                self._set_file_highlight(len(file_entries) - 1)
            return
        self._section(self._active_section).scroll_body_end()

    def scroll_active(self, delta: float) -> None:
        if self.dependency_section_is_active():
            self._move_dependency_highlight(int(delta))
            return
        if self.file_section_is_active():
            self._move_file_highlight(int(delta))
            return
        self._section(self._active_section).scroll_body_by(delta)

    def active_page_step(self) -> int:
        if self.dependency_section_is_active():
            option_list = self.query_one("#detail-option-list-1", DetailOptionList)
            return max(1, option_list.size.height)
        if self.file_section_is_active():
            option_list = self.query_one("#detail-option-list-2", DetailOptionList)
            return max(1, option_list.size.height)
        return self._section(self._active_section).page_step()

    def dependency_section_is_active(self) -> bool:
        return self._active_section == 1

    def file_section_is_active(self) -> bool:
        return self._active_section == 2

    def dependency_matchspec_at(self, index: int) -> str | None:
        entries = self._current_dependency_entries()
        if index < 0 or index >= len(entries):
            return None
        return entries[index].matchspec

    def selected_dependency_matchspec(self) -> str | None:
        option_list = self.query_one("#detail-option-list-1", DetailOptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            return None
        return self.dependency_matchspec_at(highlighted)

    def file_path_at(self, index: int) -> str | None:
        entries = self._current_file_entries()
        if index < 0 or index >= len(entries):
            return None
        return entries[index].path

    def selected_file_path(self) -> str | None:
        option_list = self.query_one("#detail-option-list-2", DetailOptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            return None
        return self.file_path_at(highlighted)

    def file_size_at(self, index: int) -> int | None:
        entries = self._current_file_entries()
        if index < 0 or index >= len(entries):
            return None
        return entries[index].size_in_bytes

    def selected_file_size_in_bytes(self) -> int | None:
        option_list = self.query_one("#detail-option-list-2", DetailOptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            return None
        return self.file_size_at(highlighted)

    def _section(self, index: int) -> DetailSection:
        return list(self.query(DetailSection))[index]

    def _active_dependency_tab(self) -> DependencyTab:
        return DEPENDENCY_TABS[self._dependency_tab_index]

    def _apply_section_state(self) -> None:
        for index, section in enumerate(self.query(DetailSection)):
            section.set_active(index == self._active_section)
        if self._details is None:
            return
        self._section(0).update_header(self._render_section_header(0, "Metadata"))
        self._section(1).update_header(self._render_dependency_header())
        self._section(2).update_header(self._render_section_header(2, "Files"))

    def _refresh_sections(self) -> None:
        if self._details is None:
            return

        self._section(0).update_header(self._render_section_header(0, "Metadata"))
        self._section(0).update_body("\n".join(self._details.metadata_lines))

        self._refresh_dependency_section()

        self._section(2).update_header(self._render_section_header(2, "Files"))
        self._file_entries = self._file_entries_for_details()
        self._section(2).update_options(
            [entry.label for entry in self._file_entries],
            highlighted=self._file_highlighted,
        )

        self._apply_section_state()

    def _refresh_dependency_section(self) -> None:
        if self._details is None:
            return

        active_tab = self._active_dependency_tab()
        dependency_section = self._section(1)
        dependency_section.update_header(self._render_dependency_header())
        self._dependency_entries = {
            tab: self._dependency_entries_for_tab(tab) for tab in DEPENDENCY_TABS
        }
        dependency_section.update_options(
            [entry.label for entry in self._dependency_entries[active_tab]],
            highlighted=self._dependency_highlighted[active_tab],
        )

    def _dependency_lines(self, tab: DependencyTab) -> tuple[str, ...]:
        assert self._details is not None
        if tab == "dependencies":
            return self._details.dependencies or ("No dependencies.",)
        if tab == "constraints":
            return self._details.constraints or ("No constraints.",)
        return self._details.run_exports or ("No run exports.",)

    def _current_dependency_entries(self) -> tuple[DependencyListEntry, ...]:
        return self._dependency_entries[self._active_dependency_tab()]

    def _dependency_entries_for_tab(
        self, tab: DependencyTab
    ) -> tuple[DependencyListEntry, ...]:
        lines = self._dependency_lines(tab)
        if tab == "run_exports":
            return tuple(
                DependencyListEntry(
                    label=self._plain_text(line),
                    matchspec=self._run_export_matchspec(line),
                )
                for line in lines
            )
        return tuple(
            DependencyListEntry(
                label=self._plain_text(line),
                matchspec=None if line.startswith("No ") else self._plain_text(line),
            )
            for line in lines
        )

    def _move_dependency_highlight(self, delta: int) -> None:
        option_list = self.query_one("#detail-option-list-1", DetailOptionList)
        highlighted = option_list.highlighted or 0
        self._set_dependency_highlight(highlighted + delta)

    def _set_dependency_highlight(self, index: int) -> None:
        entries = self._current_dependency_entries()
        if not entries:
            return
        highlighted = max(0, min(index, len(entries) - 1))
        active_tab = self._active_dependency_tab()
        self._dependency_highlighted[active_tab] = highlighted
        self.query_one(
            "#detail-option-list-1", DetailOptionList
        ).highlighted = highlighted

    def _current_file_entries(self) -> tuple[FileListEntry, ...]:
        return self._file_entries

    def _file_entries_for_details(self) -> tuple[FileListEntry, ...]:
        assert self._details is not None
        if self._details.file_paths:
            return tuple(
                FileListEntry(
                    label=(
                        f"{package_file.path}"
                        f" ({format_human_byte_size(package_file.size_in_bytes)})"
                        if package_file.size_in_bytes is not None
                        else package_file.path
                    ),
                    path=package_file.path,
                    size_in_bytes=package_file.size_in_bytes,
                )
                for package_file in self._details.file_paths
            )
        return tuple(
            FileListEntry(label=line, path=None) for line in self._details.files
        )

    def _move_file_highlight(self, delta: int) -> None:
        option_list = self.query_one("#detail-option-list-2", DetailOptionList)
        highlighted = option_list.highlighted or 0
        self._set_file_highlight(highlighted + delta)

    def _set_file_highlight(self, index: int) -> None:
        entries = self._current_file_entries()
        if not entries:
            return
        highlighted = max(0, min(index, len(entries) - 1))
        self._file_highlighted = highlighted
        self.query_one(
            "#detail-option-list-2", DetailOptionList
        ).highlighted = highlighted

    def _render_dependency_tabs(self) -> Text:
        if self._details is None:
            labels = {
                "dependencies": "Dependencies",
                "constraints": "Constraints",
                "run_exports": "Run exports",
            }
        else:
            labels = {
                "dependencies": f"Dependencies ({len(self._details.dependencies)})",
                "constraints": f"Constraints ({len(self._details.constraints)})",
                "run_exports": f"Run exports ({len(self._details.run_exports)})",
            }
        tab_text = Text()
        for index, tab in enumerate(DEPENDENCY_TABS):
            if index:
                tab_text.append(" - ", style=INACTIVE_TAB_STYLE)
            tab_text.append_text(
                self._render_clickable_dependency_tab(
                    tab,
                    labels[tab],
                    active=tab == self._active_dependency_tab(),
                    pane_active=self._pane_selected and self._active_section == 1,
                )
            )
        return tab_text

    def _render_section_header(self, index: int, label: str) -> Text:
        style = (
            ACTIVE_SECTION_TITLE_STYLE
            if self._pane_selected and index == self._active_section
            else INACTIVE_SECTION_TITLE_STYLE
        )
        return Text(f"[{index + 1}] {label}", style=style)

    def _render_dependency_header(self) -> Text:
        header = self._render_section_header(1, "")
        header.append_text(self._render_dependency_tabs())
        return header

    @staticmethod
    def _render_clickable_dependency_tab(
        tab: DependencyTab, label: str, *, active: bool, pane_active: bool
    ) -> Text:
        text = Text(label)
        text.stylize(
            ACTIVE_TAB_STYLE
            if active and pane_active
            else INACTIVE_SELECTED_TAB_STYLE
            if active
            else INACTIVE_TAB_STYLE
        )
        text.stylize(
            Style(meta={"@click": (DETAIL_SELECT_DEPENDENCY_TAB_ACTION, (tab,))})
        )
        return text

    @staticmethod
    def _plain_text(value: str) -> str:
        return value.replace(r"\[", "[").replace(r"\]", "]")

    @classmethod
    def _run_export_matchspec(cls, value: str) -> str | None:
        plain_value = cls._plain_text(value)
        if ": " not in plain_value:
            return None if plain_value.startswith("No ") else plain_value
        return plain_value.split(": ", 1)[1]


class MainPanel(Vertical):
    can_focus = True
    _vim_g_pending = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pane_selected = False

    @staticmethod
    def _page_step(height: int) -> int:
        return max(1, height)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="main-placeholder-scroll"):
            yield Static(
                "Main panel placeholder.\n\nSelect a package in the sidebar.",
                id="main-placeholder",
            )
        yield VersionDetailsView()

    def on_mount(self) -> None:
        self.show_placeholder(
            "Main panel placeholder.\n\nSelect a package in the sidebar."
        )
        self._set_placeholder_title(selected=False)

    def on_click(self, event: Click) -> None:
        from pixi_browse.tui.app import CondaMetadataTui

        cast(CondaMetadataTui, self.app)._set_selected_pane("main")
        self.focus()
        event.stop()

    def show_placeholder(self, content: str | Text) -> None:
        placeholder = self.query_one("#main-placeholder-scroll", VerticalScroll)
        placeholder.display = True
        self._set_placeholder_title(selected=self._pane_selected)
        self.query_one("#main-placeholder", Static).update(content)
        self.query_one("#version-details-view", VersionDetailsView).display = False

    def show_version_details(self, details: VersionDetailsData) -> None:
        placeholder = self.query_one("#main-placeholder-scroll", VerticalScroll)
        placeholder.display = False
        placeholder.border_title = ""
        version_details = self.query_one("#version-details-view", VersionDetailsView)
        version_details.set_details(details)
        version_details.set_pane_selected(self._pane_selected)
        version_details.display = True

    def set_pane_selected(self, selected: bool) -> None:
        self._pane_selected = selected
        if self._showing_version_details():
            self.query_one(
                "#version-details-view", VersionDetailsView
            ).set_pane_selected(selected)
        else:
            self._set_placeholder_title(selected=selected)

    def _set_placeholder_title(self, *, selected: bool) -> None:
        self.query_one("#main-placeholder-scroll", VerticalScroll).border_title = Text(
            "[1] Details",
            style=ACTIVE_SECTION_TITLE_STYLE
            if selected
            else INACTIVE_SECTION_TITLE_STYLE,
        )

    def on_focus(self) -> None:
        from pixi_browse.tui.app import CondaMetadataTui

        app = cast(CondaMetadataTui, self.app)
        app._set_selected_pane("main")
        app._update_filter_indicator()

    def on_blur(self) -> None:
        from pixi_browse.tui.app import CondaMetadataTui

        cast(CondaMetadataTui, self.app)._update_filter_indicator()

    def set_active_section(self, index: int) -> None:
        self.query_one("#version-details-view", VersionDetailsView).set_active_section(
            index
        )

    def cycle_dependency_tab(self, direction: int) -> None:
        self.query_one(
            "#version-details-view", VersionDetailsView
        ).cycle_dependency_tab(direction)

    def dependency_section_is_active(self) -> bool:
        return self.query_one(
            "#version-details-view", VersionDetailsView
        ).dependency_section_is_active()

    def file_section_is_active(self) -> bool:
        return self.query_one(
            "#version-details-view", VersionDetailsView
        ).file_section_is_active()

    def selected_dependency_matchspec(self) -> str | None:
        return self.query_one(
            "#version-details-view", VersionDetailsView
        ).selected_dependency_matchspec()

    def dependency_matchspec_at(self, index: int) -> str | None:
        return self.query_one(
            "#version-details-view", VersionDetailsView
        ).dependency_matchspec_at(index)

    def selected_file_path(self) -> str | None:
        return self.query_one(
            "#version-details-view", VersionDetailsView
        ).selected_file_path()

    def selected_file_size_in_bytes(self) -> int | None:
        return self.query_one(
            "#version-details-view", VersionDetailsView
        ).selected_file_size_in_bytes()

    def file_path_at(self, index: int) -> str | None:
        return self.query_one("#version-details-view", VersionDetailsView).file_path_at(
            index
        )

    def file_size_at(self, index: int) -> int | None:
        return self.query_one("#version-details-view", VersionDetailsView).file_size_at(
            index
        )

    def set_dependency_tab(self, tab: DependencyTab) -> None:
        self.query_one("#version-details-view", VersionDetailsView).set_dependency_tab(
            tab
        )

    def cycle_active_section(self, direction: int) -> None:
        self.query_one(
            "#version-details-view", VersionDetailsView
        ).cycle_active_section(direction)

    def reset_scroll(self) -> None:
        if self._showing_version_details():
            self.query_one(
                "#version-details-view", VersionDetailsView
            ).scroll_home_active()
            return
        self.query_one("#main-placeholder-scroll", VerticalScroll).scroll_home(
            animate=False,
            immediate=True,
            x_axis=False,
        )

    def scroll_main(self, delta: float) -> None:
        if self._showing_version_details():
            self.query_one("#version-details-view", VersionDetailsView).scroll_active(
                delta
            )
            return
        placeholder = self.query_one("#main-placeholder-scroll", VerticalScroll)
        placeholder.scroll_to(y=placeholder.scroll_y + delta, animate=False)

    def scroll_home_main(self) -> None:
        if self._showing_version_details():
            self.query_one(
                "#version-details-view", VersionDetailsView
            ).scroll_home_active()
            return
        self.query_one("#main-placeholder-scroll", VerticalScroll).scroll_to(
            y=0,
            animate=False,
        )

    def scroll_end_main(self) -> None:
        if self._showing_version_details():
            self.query_one(
                "#version-details-view", VersionDetailsView
            ).scroll_end_active()
            return
        self.query_one("#main-placeholder-scroll", VerticalScroll).scroll_end(
            animate=False
        )

    def _showing_version_details(self) -> bool:
        return self.query_one("#version-details-view", VersionDetailsView).display

    def showing_version_details(self) -> bool:
        return self._showing_version_details()

    def current_page_step(self) -> int:
        if self._showing_version_details():
            return self.query_one(
                "#version-details-view", VersionDetailsView
            ).active_page_step()

        placeholder = self.query_one("#main-placeholder-scroll", VerticalScroll)
        return self._page_step(placeholder.size.height)

    def on_key(self, event: Key) -> None:
        page_height = self.current_page_step()
        character = event.character

        if self._showing_version_details():
            dependency_section_is_active = self.dependency_section_is_active()
            if event.key == "tab":
                self.cycle_active_section(1)
                event.stop()
                return
            if event.key in {"shift+tab", "backtab"}:
                self.cycle_active_section(-1)
                event.stop()
                return
            if character in {"1", "2", "3"}:
                self.set_active_section(int(character) - 1)
                event.stop()
                return
            if character == "[" and dependency_section_is_active:
                self.cycle_dependency_tab(-1)
                event.stop()
                return
            if character == "]" and dependency_section_is_active:
                self.cycle_dependency_tab(1)
                event.stop()
                return

        if character == "g":
            if self._vim_g_pending:
                self.scroll_home_main()
                self._vim_g_pending = False
            else:
                self._vim_g_pending = True
            event.stop()
            return

        if character == "G":
            self.scroll_end_main()
            self._vim_g_pending = False
            event.stop()
            return

        self._vim_g_pending = False

        if event.key in {"up", "k"}:
            self.scroll_main(-1)
            event.stop()
            return
        if event.key in {"down", "j"}:
            self.scroll_main(1)
            event.stop()
            return
        if event.key == "pageup":
            self.scroll_main(-page_height)
            event.stop()
            return
        if event.key == "pagedown":
            self.scroll_main(page_height)
            event.stop()
            return
        if event.key == "ctrl+u":
            self.scroll_main(-page_height)
            event.stop()
            return
        if event.key == "ctrl+d":
            self.scroll_main(page_height)
            event.stop()
            return
        if event.key == "home":
            self.scroll_home_main()
            event.stop()
            return
        if event.key == "end":
            self.scroll_end_main()
            event.stop()
            return
        if character == "h":
            from pixi_browse.tui.app import CondaMetadataTui

            cast(CondaMetadataTui, self.app)._focus_sidebar()
            event.stop()


class CompareDetailsView(Vertical):
    can_focus = True
    _vim_g_pending = False

    def __init__(self, compare_data: VersionCompareData) -> None:
        super().__init__(
            id="compare-details-view", classes="detail-view -pane-selected"
        )
        self._compare_data = compare_data
        self._active_section = 0
        self._dependency_tab_index = 0
        self._pane_selected = True

    def compose(self) -> ComposeResult:
        yield DetailSection(
            "Metadata",
            0,
            on_activate=self.set_active_section,
            id_prefix="compare",
        )
        yield DetailSection(
            "Dependencies",
            1,
            on_activate=self.set_active_section,
            on_select_dependency_tab=self._select_dependency_tab_from_click,
            id_prefix="compare",
        )
        yield DetailSection(
            "Files",
            2,
            on_activate=self.set_active_section,
            id_prefix="compare",
        )

    def on_mount(self) -> None:
        self._refresh_sections()

    def set_compare_data(self, compare_data: VersionCompareData) -> None:
        self._compare_data = compare_data
        self._refresh_sections()

    def set_active_section(self, index: int) -> None:
        self._active_section = max(0, min(index, 2))
        self._apply_section_state()

    def cycle_active_section(self, direction: int) -> None:
        self._active_section = (self._active_section + direction) % 3
        self._apply_section_state()

    def _select_dependency_tab_from_click(self, tab: DependencyTab) -> None:
        self.select_dependency_tab(tab, focus_view=True)

    def select_dependency_tab(
        self, tab: DependencyTab, *, focus_view: bool = False
    ) -> None:
        self.set_active_section(1)
        self._dependency_tab_index = DEPENDENCY_TABS.index(tab)
        self._refresh_dependency_section()
        if focus_view:
            self.focus()

    def cycle_dependency_tab(self, direction: int) -> None:
        self._dependency_tab_index = (self._dependency_tab_index + direction) % len(
            DEPENDENCY_TABS
        )
        self._refresh_dependency_section()

    def scroll_active(self, delta: float) -> None:
        self._section(self._active_section).scroll_body_by(delta)

    def scroll_home_active(self) -> None:
        self._section(self._active_section).scroll_body_home()

    def scroll_end_active(self) -> None:
        self._section(self._active_section).scroll_body_end()

    def active_page_step(self) -> int:
        return self._section(self._active_section).page_step()

    def _section(self, index: int) -> DetailSection:
        return list(self.query(DetailSection))[index]

    def _active_dependency_tab(self) -> DependencyTab:
        return DEPENDENCY_TABS[self._dependency_tab_index]

    def _apply_section_state(self) -> None:
        for index, section in enumerate(self.query(DetailSection)):
            section.set_active(index == self._active_section)
        self._section(0).update_header(self._render_section_header(0, "Metadata"))
        self._section(1).update_header(self._render_dependency_header())
        self._section(2).update_header(self._render_section_header(2, "Files"))

    def _refresh_sections(self) -> None:
        self._section(0).update_header(self._render_section_header(0, "Metadata"))
        self._section(0).update_body(self._render_metadata_body())
        self._refresh_dependency_section()
        self._section(2).update_header(self._render_section_header(2, "Files"))
        self._section(2).update_body(self._render_file_body())
        self._apply_section_state()

    def _refresh_dependency_section(self) -> None:
        section = self._section(1)
        section.update_header(self._render_dependency_header())
        section.update_body(self._render_dependency_body(self._active_dependency_tab()))

    def _render_section_header(self, index: int, label: str) -> Text:
        return Text(
            f"[{index + 1}] {label}",
            style=(
                ACTIVE_SECTION_TITLE_STYLE
                if self._pane_selected and index == self._active_section
                else INACTIVE_SECTION_TITLE_STYLE
            ),
        )

    def _render_dependency_header(self) -> Text:
        header = self._render_section_header(1, "")
        header.append_text(self._render_dependency_tabs())
        return header

    def _render_dependency_tabs(self) -> Text:
        labels = {
            "dependencies": f"Dependencies ({len(self._compare_data.dependencies)})",
            "constraints": f"Constraints ({len(self._compare_data.constraints)})",
            "run_exports": f"Run exports ({len(self._compare_data.run_exports)})",
        }
        text = Text()
        for index, tab in enumerate(DEPENDENCY_TABS):
            if index:
                text.append(" - ", style=INACTIVE_TAB_STYLE)
            text.append_text(
                self._render_clickable_dependency_tab(
                    tab,
                    labels[tab],
                    active=tab == self._active_dependency_tab(),
                    pane_active=self._pane_selected and self._active_section == 1,
                )
            )
        return text

    @staticmethod
    def _render_clickable_dependency_tab(
        tab: DependencyTab, label: str, *, active: bool, pane_active: bool
    ) -> Text:
        text = Text(label)
        text.stylize(
            ACTIVE_TAB_STYLE
            if active and pane_active
            else INACTIVE_SELECTED_TAB_STYLE
            if active
            else INACTIVE_TAB_STYLE
        )
        text.stylize(
            Style(meta={"@click": (DETAIL_SELECT_DEPENDENCY_TAB_ACTION, (tab,))})
        )
        return text

    def _dependency_lines(self, tab: DependencyTab) -> tuple[CompareRow, ...]:
        if tab == "dependencies":
            return self._compare_data.dependencies
        if tab == "constraints":
            return self._compare_data.constraints
        return self._compare_data.run_exports

    def _render_metadata_body(self) -> RenderableType:
        return self._render_compare_table(
            self._compare_data.metadata_rows,
            label_title="Field",
            empty_message="No metadata available.",
            show_label_column=True,
        )

    def _render_dependency_body(self, tab: DependencyTab) -> RenderableType:
        return self._render_compare_table(
            self._dependency_lines(tab),
            empty_message="No dependency data.",
            show_label_column=False,
        )

    def _render_file_body(self) -> RenderableType:
        if not self._compare_data.files:
            return Text("No files listed.", style="dim")

        body = Text()
        for index, row in enumerate(self._compare_data.files):
            if index:
                body.append("\n")
            body.append(row.label, style=self._file_row_style(row))
        return body

    @staticmethod
    def _row_style(row: CompareRow) -> tuple[str, str]:
        if row.changed:
            return "red", "green"
        return "white", "white"

    @staticmethod
    def _file_row_style(row: CompareRow) -> str:
        if not row.changed:
            return "white"
        if row.left and row.right:
            return "yellow"
        if row.left:
            return "red"
        return "green"

    def _render_compare_table(
        self,
        rows: tuple[CompareRow, ...],
        *,
        empty_message: str,
        show_label_column: bool,
        label_title: str = "",
    ) -> RenderableType:
        if not rows:
            return Text(empty_message, style="dim")

        table = Table(
            box=box.SIMPLE,
            expand=True,
            show_edge=False,
            pad_edge=False,
            collapse_padding=True,
        )
        if show_label_column:
            table.add_column(label_title, style="bold", ratio=1)
        table.add_column("Left", ratio=2)
        table.add_column("Right", ratio=2)

        for row in rows:
            left_style, right_style = self._row_style(row)
            cells: list[RenderableType] = []
            if show_label_column:
                cells.append(row.label)
            cells.append(Text(row.left, style=left_style))
            cells.append(Text(row.right, style=right_style))
            table.add_row(*cells)
        return table

    def on_key(self, event: Key) -> None:
        page_height = self.active_page_step()
        character = event.character

        if event.key == "tab":
            self.cycle_active_section(1)
            event.stop()
            return
        if event.key in {"shift+tab", "backtab"}:
            self.cycle_active_section(-1)
            event.stop()
            return
        if character in {"1", "2", "3"}:
            self.set_active_section(int(character) - 1)
            event.stop()
            return
        if character == "[" and self._active_section == 1:
            self.cycle_dependency_tab(-1)
            event.stop()
            return
        if character == "]" and self._active_section == 1:
            self.cycle_dependency_tab(1)
            event.stop()
            return
        if character == "g":
            if self._vim_g_pending:
                self.scroll_home_active()
                self._vim_g_pending = False
            else:
                self._vim_g_pending = True
            event.stop()
            return
        if character == "G":
            self.scroll_end_active()
            self._vim_g_pending = False
            event.stop()
            return

        self._vim_g_pending = False

        if event.key in {"up", "k"}:
            self.scroll_active(-1)
            event.stop()
            return
        if event.key in {"down", "j"}:
            self.scroll_active(1)
            event.stop()
            return
        if event.key in {"pageup", "ctrl+u"}:
            self.scroll_active(-page_height)
            event.stop()
            return
        if event.key in {"pagedown", "ctrl+d"}:
            self.scroll_active(page_height)
            event.stop()
            return
        if event.key == "home":
            self.scroll_home_active()
            event.stop()
            return
        if event.key == "end":
            self.scroll_end_active()
            event.stop()


class CompareScreen(Screen[None]):
    DEFAULT_CSS = """
    #compare-root {
        height: 1fr;
        padding: 0;
    }

    #compare-title {
        height: auto;
        padding: 0 0 1 0;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("tab", "next_section", show=False, priority=True),
        Binding("shift+tab", "previous_section", show=False, priority=True),
        Binding("x", "swap_sides", show=False),
        Binding("escape", "dismiss", show=False),
        Binding("q", "dismiss", show=False),
    ]

    def __init__(self, compare_data: VersionCompareData) -> None:
        super().__init__()
        self._compare_data = compare_data

    def compose(self) -> ComposeResult:
        with Vertical(id="compare-root"):
            yield Static(self._title_text(), id="compare-title", markup=False)
            yield CompareDetailsView(self._compare_data)

    def on_mount(self) -> None:
        self.query_one("#compare-details-view", CompareDetailsView).focus()

    def _title_text(self) -> str:
        left = self._selection_label(self._compare_data.left_selection)
        right = self._selection_label(self._compare_data.right_selection)
        return f"Compare\n{left}\nvs\n{right}"

    @staticmethod
    def _selection_label(selection: CompareSelection) -> str:
        entry = selection.entry
        return (
            f"{selection.package_name} {entry.version} {entry.build} [{entry.subdir}]"
        )

    def action_next_section(self) -> None:
        self.query_one(
            "#compare-details-view", CompareDetailsView
        ).cycle_active_section(1)

    def action_previous_section(self) -> None:
        self.query_one(
            "#compare-details-view", CompareDetailsView
        ).cycle_active_section(-1)

    @staticmethod
    def _swap_rows(rows: tuple[CompareRow, ...]) -> tuple[CompareRow, ...]:
        return tuple(
            CompareRow(
                label=row.label,
                left=row.right,
                right=row.left,
                changed=row.changed,
            )
            for row in rows
        )

    @classmethod
    def _swapped_compare_data(
        cls, compare_data: VersionCompareData
    ) -> VersionCompareData:
        return VersionCompareData(
            left_selection=compare_data.right_selection,
            right_selection=compare_data.left_selection,
            metadata_rows=cls._swap_rows(compare_data.metadata_rows),
            dependencies=cls._swap_rows(compare_data.dependencies),
            constraints=cls._swap_rows(compare_data.constraints),
            run_exports=cls._swap_rows(compare_data.run_exports),
            files=cls._swap_rows(compare_data.files),
        )

    def action_swap_sides(self) -> None:
        self._compare_data = self._swapped_compare_data(self._compare_data)
        self.query_one("#compare-title", Static).update(self._title_text())
        self.query_one("#compare-details-view", CompareDetailsView).set_compare_data(
            self._compare_data
        )
        self.query_one("#compare-details-view", CompareDetailsView).focus()

    async def action_dismiss(self, result: None = None) -> None:
        self.dismiss(result)


class SidebarPanel(Vertical):
    def on_click(self, event: Click) -> None:
        from pixi_browse.tui.app import CondaMetadataTui

        app = cast(CondaMetadataTui, self.app)
        app._set_selected_pane("sidebar")
        app.query_one("#sidebar-list").focus()
        event.stop()


class MatchSpecScreen(ModalScreen[MatchSpec | Empty | None]):
    DEFAULT_CSS = """
    MatchSpecScreen {
        align: center middle;
        background: $background 60%;
    }

    #matchspec-dialog {
        width: 72;
        max-width: 90%;
        height: auto;
        border: round #ec4899;
        background: $surface;
        padding: 1 2;
    }

    #matchspec-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #matchspec-help {
        color: $text-muted;
        margin-top: 1;
    }

    #matchspec-input {
        border: tall #ec4899;
    }

    #matchspec-input:focus {
        border: tall #ec4899;
    }

    #matchspec-input > .input--selection {
        background: #ec4899;
        color: #ffffff;
    }

    #matchspec-error {
        color: $error;
        min-height: 1;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", show=False),
        Binding("q", "dismiss", show=False),
    ]

    def __init__(
        self, initial_value: str = "", *, select_on_focus: bool = True
    ) -> None:
        super().__init__()
        self._initial_value = initial_value
        self._select_on_focus = select_on_focus

    def compose(self) -> ComposeResult:
        with Vertical(id="matchspec-dialog"):
            yield Static("MatchSpec", id="matchspec-title")
            yield Input(
                value=self._initial_value,
                placeholder="numpy >=2",
                select_on_focus=self._select_on_focus,
                id="matchspec-input",
            )
            yield Static("Leave empty to query everything.", id="matchspec-help")
            yield Static("", id="matchspec-error")

    def on_mount(self) -> None:
        self.query_one("#matchspec-input", Input).focus()

    @staticmethod
    def validate_matchspec(value: str) -> MatchSpec | Empty:
        query = value.strip()
        if not query:
            return EMPTY_MATCHSPEC_RESULT
        return MatchSpec(query, exact_names_only=False)

    def _show_error(self, message: str) -> None:
        self.query_one("#matchspec-error", Static).update(Text(message))

    def _update_validation_error(self, value: str) -> None:
        try:
            self.validate_matchspec(value)
        except InvalidMatchSpecError as exc:
            self._show_error(str(exc))
            return

        self._show_error("")

    @on(Input.Changed, "#matchspec-input")
    def _validate_input(self, event: Input.Changed) -> None:
        self._update_validation_error(event.value)

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        event.stop()
        try:
            result = self.validate_matchspec(event.value)
        except InvalidMatchSpecError as exc:
            self._show_error(str(exc))
            return

        self.dismiss(result)

    async def action_dismiss(self, result: MatchSpec | Empty | None = None) -> None:
        self.dismiss(result)


class FileActionScreen(ModalScreen[FileAction | None]):
    DEFAULT_CSS = """
    FileActionScreen {
        align: center middle;
        background: $background 60%;
    }

    #file-action-dialog {
        width: 72;
        max-width: 90%;
        height: auto;
        border: round #ec4899;
        background: $surface;
        padding: 1 2;
    }

    #file-action-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #file-action-path {
        color: $text-muted;
        margin-bottom: 1;
    }

    #file-action-list {
        border: none;
        background: $background;
        padding: 0 0 0 1;
    }

    #file-action-list > .option-list--option-highlighted {
        color: #ffffff;
        background: #ec4899;
        text-style: bold;
    }

    #file-action-list > .option-list--option-hover {
        color: #f9a8d4;
        background: #4a2233;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", show=False),
        Binding("q", "dismiss", show=False),
    ]

    _ACTIONS: tuple[tuple[FileAction, str], ...] = (
        ("preview", "Preview"),
        ("download", "Download as file"),
    )

    def __init__(self, file_path: str) -> None:
        super().__init__()
        self._file_path = file_path

    def compose(self) -> ComposeResult:
        with Vertical(id="file-action-dialog"):
            yield Static("File Action", id="file-action-title")
            yield Static(self._file_path, id="file-action-path", markup=False)
            yield OptionList(
                *(label for _, label in self._ACTIONS),
                id="file-action-list",
                markup=False,
            )

    def on_mount(self) -> None:
        self.query_one("#file-action-list", OptionList).focus()

    @on(OptionList.OptionSelected, "#file-action-list")
    def _select_action(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        action, _label = self._ACTIONS[event.option_index]
        self.dismiss(action)

    async def action_dismiss(self, result: FileAction | None = None) -> None:
        self.dismiss(result)


class FilePreviewScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    FilePreviewScreen {
        align: center middle;
        background: $background 60%;
    }

    #file-preview-dialog {
        width: 120;
        max-width: 95%;
        height: 90%;
        border: round #ec4899;
        background: $surface;
        padding: 1 2;
    }

    #file-preview-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #file-preview-scroll {
        height: 1fr;
        border: round #ec4899;
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }

    #file-preview-body {
        color: $text;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", show=False),
        Binding("q", "dismiss", show=False),
        Binding("up,k", "scroll_up", show=False),
        Binding("down,j", "scroll_down", show=False),
        Binding("pageup,ctrl+u", "page_up", show=False),
        Binding("pagedown,ctrl+d", "page_down", show=False),
        Binding("home", "scroll_home", show=False),
        Binding("end", "scroll_end", show=False),
        Binding("g", "scroll_home", show=False),
        Binding("G", "scroll_end", show=False),
    ]

    def __init__(self, title: str, content: str) -> None:
        super().__init__()
        self._title = title
        self._content = content

    def compose(self) -> ComposeResult:
        with Vertical(id="file-preview-dialog"):
            yield Static(self._title, id="file-preview-title", markup=False)
            with VerticalScroll(id="file-preview-scroll"):
                yield Static(self._content, id="file-preview-body", markup=False)

    def on_mount(self) -> None:
        self.query_one("#file-preview-scroll", VerticalScroll).focus()

    def _scroll(self) -> VerticalScroll:
        return self.query_one("#file-preview-scroll", VerticalScroll)

    def action_scroll_up(self) -> None:
        scroll = self._scroll()
        scroll.scroll_to(y=max(0, scroll.scroll_y - 1), animate=False)

    def action_scroll_down(self) -> None:
        scroll = self._scroll()
        scroll.scroll_to(y=scroll.scroll_y + 1, animate=False)

    def action_page_up(self) -> None:
        scroll = self._scroll()
        scroll.scroll_to(
            y=max(0, scroll.scroll_y - max(1, scroll.size.height)), animate=False
        )

    def action_page_down(self) -> None:
        scroll = self._scroll()
        scroll.scroll_to(y=scroll.scroll_y + max(1, scroll.size.height), animate=False)

    def action_scroll_home(self) -> None:
        self._scroll().scroll_home(animate=False, immediate=True, x_axis=False)

    def action_scroll_end(self) -> None:
        self._scroll().scroll_end(animate=False)

    async def action_dismiss(self, result: None = None) -> None:
        del result
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: $background 60%;
    }

    #help-dialog {
        width: 72;
        max-width: 90%;
        height: auto;
        max-height: 90%;
        border: round #ec4899;
        background: $surface;
        padding: 1 2;
    }

    #help-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #help-body {
        color: $text;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", show=False),
        Binding("q", "dismiss", show=False),
        Binding("question_mark", "dismiss", show=False),
    ]

    def __init__(self, help_text: str, *, version: str) -> None:
        super().__init__()
        self._help_text = help_text
        self._version = version

    def _title_text(self) -> str:
        return f"pixi-browse v{self._version}"

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static(self._title_text(), id="help-title")
            yield Static(self._help_text, id="help-body")

    async def action_dismiss(self, result: None = None) -> None:
        self.dismiss(result)
