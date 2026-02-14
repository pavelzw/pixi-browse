from __future__ import annotations


def fuzzy_score(query: str, candidate: str) -> int | None:
    """Score a candidate using subsequence matching; higher scores are better."""
    query = query.casefold().strip()
    candidate = candidate.casefold()
    if not query:
        return 0

    cursor = -1
    gap_penalty = 0
    run_length = 0
    longest_run = 0

    for char in query:
        index = candidate.find(char, cursor + 1)
        if index == -1:
            return None
        if cursor != -1:
            gap_penalty += index - cursor - 1
        if index == cursor + 1:
            run_length += 1
        else:
            run_length = 1
        longest_run = max(longest_run, run_length)
        cursor = index

    prefix_bonus = 120 if candidate.startswith(query) else 0
    length_penalty = len(candidate) - len(query)
    return prefix_bonus + (longest_run * 20) - (gap_penalty * 2) - length_penalty
