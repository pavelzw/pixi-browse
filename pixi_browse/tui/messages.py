from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from textual.message import Message


@dataclass
class PaneSelected(Message):
    """A pane became the selected one, by a click or by taking focus."""

    pane: Literal["sidebar", "main"]


class SidebarFocusRequested(Message):
    """The main panel is done with the focus and hands it to the sidebar."""


class FilterIndicatorChanged(Message):
    """Something a pane title, the footer or the indicators are drawn from
    changed, so the app should redraw them."""


class ListSearchEnded(Message):
    """A view left the ``/`` search on its own, because the list the search
    narrowed is no longer showing."""
