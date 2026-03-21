from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.events import Click, Key
from textual.screen import ModalScreen
from textual.widgets import Static

from pixi_browse.models import DependencyTab, VersionDetailsData

DEPENDENCY_TABS: tuple[DependencyTab, ...] = (
    "dependencies",
    "constraints",
    "run_exports",
)
ACTIVE_SECTION_TITLE_STYLE = Style(color="bright_blue", bold=True)
INACTIVE_SECTION_TITLE_STYLE = Style(dim=True)
ACTIVE_TAB_STYLE = Style(color="bright_blue", bold=True)
INACTIVE_SELECTED_TAB_STYLE = Style(color="blue", bold=False)
INACTIVE_TAB_STYLE = INACTIVE_SECTION_TITLE_STYLE
TAB_HINT_STYLE = Style(color="default", dim=True)


class DetailSection(Vertical):
    def __init__(self, title: str, index: int, *, show_tabs: bool = False) -> None:
        super().__init__(classes="detail-section")
        self._index = index
        del title, show_tabs
        self.auto_links = False
        self.styles.border_title_align = "left"

    def compose(self) -> ComposeResult:
        with VerticalScroll(id=f"detail-scroll-{self._index}", classes="detail-scroll"):
            yield Static(id=f"detail-body-{self._index}", classes="detail-body")

    def on_click(self, event: Click) -> None:
        style = event.style
        meta = style.meta if style is not None else None
        click_meta = meta.get("@click") if meta is not None else None
        if click_meta is not None:
            action_name, args = click_meta
            if action_name == "app.select_dependency_tab":
                self.app.query_one(
                    "#version-details-view", VersionDetailsView
                ).select_dependency_tab(
                    *args,
                    focus_main_panel=False,
                )
                event.stop()
                return
        self.app.query_one(
            "#version-details-view", VersionDetailsView
        ).activate_section(
            self._index,
            focus_main_panel=True,
        )
        event.stop()

    def update_header(self, title: str | Text) -> None:
        self.border_title = title

    def update_body(self, body: str | Text) -> None:
        self.query_one(f"#detail-body-{self._index}", Static).update(body)

    def set_active(self, active: bool) -> None:
        self.set_class(active, "-active")
        self.set_class(not active, "-collapsed")

    def scroll_body_home(self) -> None:
        self.query_one(f"#detail-scroll-{self._index}", VerticalScroll).scroll_home(
            animate=False,
            immediate=True,
            x_axis=False,
        )

    def scroll_body_end(self) -> None:
        self.query_one(f"#detail-scroll-{self._index}", VerticalScroll).scroll_end(
            animate=False
        )

    def scroll_body_by(self, delta: float) -> None:
        scroll = self.query_one(f"#detail-scroll-{self._index}", VerticalScroll)
        scroll.scroll_to(y=scroll.scroll_y + delta, animate=False)

    def page_step(self) -> int:
        scroll = self.query_one(f"#detail-scroll-{self._index}", VerticalScroll)
        return max(1, scroll.size.height)


class VersionDetailsView(Vertical):
    def __init__(self) -> None:
        super().__init__(id="version-details-view")
        self._details: VersionDetailsData | None = None
        self._active_section = 0
        self._dependency_tab_index = 0

    def compose(self) -> ComposeResult:
        yield DetailSection("Metadata", 0)
        yield DetailSection("Dependencies", 1, show_tabs=True)
        yield DetailSection("Files", 2)

    def set_details(self, details: VersionDetailsData) -> None:
        self._details = details
        self.display = True
        self._refresh_sections()

    def set_active_section(self, index: int) -> None:
        self._active_section = max(0, min(index, 2))
        self._apply_section_state()

    def activate_section(self, index: int, *, focus_main_panel: bool = False) -> None:
        self.set_active_section(index)
        if focus_main_panel:
            self.app.query_one("#main-panel", MainPanel).focus()

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
        self._section(self._active_section).scroll_body_home()

    def scroll_end_active(self) -> None:
        self._section(self._active_section).scroll_body_end()

    def scroll_active(self, delta: float) -> None:
        self._section(self._active_section).scroll_body_by(delta)

    def active_page_step(self) -> int:
        return self._section(self._active_section).page_step()

    def dependency_section_is_active(self) -> bool:
        return self._active_section == 1

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
        self._section(2).update_body("\n".join(self._details.files))

        self._apply_section_state()

    def _refresh_dependency_section(self) -> None:
        if self._details is None:
            return

        dependency_section = self._section(1)
        dependency_section.update_header(self._render_dependency_header())
        dependency_section.update_body(
            "\n".join(self._dependency_lines(self._active_dependency_tab()))
        )

    def _dependency_lines(self, tab: DependencyTab) -> tuple[str, ...]:
        assert self._details is not None
        if tab == "dependencies":
            return self._details.dependencies or ("No dependencies.",)
        if tab == "constraints":
            return self._details.constraints or ("No constraints.",)
        return self._details.run_exports or ("No run exports.",)

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
                    pane_active=self._active_section == 1,
                )
            )
        return tab_text

    def _render_section_header(self, index: int, label: str) -> Text:
        style = (
            ACTIVE_SECTION_TITLE_STYLE
            if index == self._active_section
            else INACTIVE_SECTION_TITLE_STYLE
        )
        return Text(f"[{index + 1}] {label}", style=style)

    def _render_dependency_header(self) -> Text:
        header = self._render_section_header(1, "")
        header.append_text(self._render_dependency_tabs())
        if self._active_section == 1:
            header.append("  [ / ]", style=TAB_HINT_STYLE)
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
            Style(
                meta={
                    "@click": (
                        "app.select_dependency_tab",
                        (tab,),
                    )
                }
            )
        )
        return text


class MainPanel(Vertical):
    can_focus = True
    _vim_g_pending = False

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

    def on_click(self, event: Click) -> None:
        self.focus()
        event.stop()

    def show_placeholder(self, content: str | Text) -> None:
        placeholder = self.query_one("#main-placeholder-scroll", VerticalScroll)
        placeholder.display = True
        placeholder.border_title = Text("[1] Details", style="bold")
        self.query_one("#main-placeholder", Static).update(content)
        self.query_one("#version-details-view", VersionDetailsView).display = False

    def show_version_details(self, details: VersionDetailsData) -> None:
        placeholder = self.query_one("#main-placeholder-scroll", VerticalScroll)
        placeholder.display = False
        placeholder.border_title = ""
        version_details = self.query_one("#version-details-view", VersionDetailsView)
        version_details.set_details(details)
        version_details.display = True

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
            self.app.query_one("#sidebar-list").focus()
            event.stop()


class SidebarPanel(Vertical):
    def on_click(self, event: Click) -> None:
        self.app.query_one("#sidebar-list").focus()
        event.stop()


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
        border: round $accent;
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
