from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from textual.events import Key

# Which list the ``/`` search narrows: the sidebar's version list, the version
# details in the main panel or the compare screen's file list.
type ListSearchScope = Literal["versions", "details", "compare"]


class ListSearchState:
    """The ``/`` search of a list: its query, the list it runs on and the keys
    that edit it.

    The app owns one and passes it to the list views, because ``on_key`` needs
    the answer to "was this key typed into the search?" while it can still stop
    the event; a posted message would arrive too late. Every change calls
    ``apply``, with which the app narrows the lists and redraws the footer.
    """

    def __init__(self, apply: Callable[[], None]) -> None:
        self._apply = apply
        self.active = False
        self.query = ""
        self.scope: ListSearchScope | None = None

    def start(self, scope: ListSearchScope) -> None:
        """Begin searching ``scope`` with an empty query."""
        self.active = True
        self.query = ""
        self.scope = scope
        self._apply()

    def stop(self) -> None:
        """Leave the search and show the full list again."""
        if not self.active:
            return
        self.reset()
        self._apply()

    def reset(self) -> None:
        """Forget the search without applying it, for when the list it ran on is
        gone along with the view that held it."""
        self.active = False
        self.query = ""
        self.scope = None

    def append(self, char: str) -> None:
        self.query += char
        self._apply()

    def consume_key(self, event: Key, scope: ListSearchScope) -> bool:
        """Feed a key of the focused list view into the ``/`` search.

        Mirrors the package search: printable characters extend the query,
        backspace shortens it and escape leaves the search. Everything else
        (the arrow keys, paging, ``Enter``) is left to the list itself. Only the
        list the search runs on may type into it, hence ``scope``.
        """
        if not self.active or self.scope != scope:
            return False
        if event.key == "escape":
            self.stop()
            return True
        if event.key == "backspace":
            self.query = self.query[:-1]
            self._apply()
            return True
        if event.key == "space":
            self.append(" ")
            return True
        if event.character is not None and event.character.isprintable():
            self.append(event.character)
            return True
        return False
