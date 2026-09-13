from __future__ import annotations

from collections.abc import Callable, Iterable


def substring_position(query: str, candidate: str) -> int | None:
    """Where ``query`` occurs in ``candidate``, or ``None`` if it does not.

    The comparison ignores case and the query's surrounding whitespace; earlier
    positions are better matches.
    """
    query = query.casefold().strip()
    candidate = candidate.casefold()
    if not query:
        return 0
    position = candidate.find(query)
    return None if position == -1 else position


def substring_filter[ItemT](
    query: str, items: Iterable[ItemT], *, key: Callable[[ItemT], str]
) -> list[ItemT]:
    """The items whose ``key`` contains ``query``, best match first.

    Matches close to the start of the text come first, then the shortest text,
    then the text itself so the order never depends on the order the items came
    in.
    """
    matched: list[tuple[int, int, str, ItemT]] = []
    for item in items:
        text = key(item)
        position = substring_position(query, text)
        if position is not None:
            matched.append((position, len(text), text, item))
    matched.sort(key=lambda entry: entry[:3])
    return [item for _, _, _, item in matched]
