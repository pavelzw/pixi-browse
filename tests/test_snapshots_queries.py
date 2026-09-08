"""Snapshot tests of the package search, MatchSpec and who-needs queries, the
platform selector and channel switching, all against the offline channels."""

from __future__ import annotations

from rattler.match_spec import MatchSpec
from rattler.platform import Platform
from textual.pilot import Pilot

from pixi_browse.tui import ChannelScreen, MatchSpecScreen
from tests.helpers import (
    BIOCONDA_CHANNEL,
    MAIN_CHANNEL,
    MISSING_CHANNEL,
    TERMINAL_SIZE,
    AppFactory,
    SnapCompare,
    open_versions,
    type_text,
    wait_for_idle,
    wait_for_screen,
)


async def run_matchspec_query(pilot: Pilot[None], query: str) -> None:
    await pilot.press("m")
    await pilot.pause()
    await type_text(pilot, query)
    await pilot.press("enter")
    await wait_for_idle(pilot)


async def run_whoneeds_query(pilot: Pilot[None], query: str) -> None:
    await pilot.press("w")
    await pilot.pause()
    await type_text(pilot, query)
    await pilot.press("enter")
    await wait_for_idle(pilot)


async def open_channel_screen(pilot: Pilot[None]) -> None:
    await pilot.press("c")
    await wait_for_screen(pilot, ChannelScreen)


async def add_channel(pilot: Pilot[None], channel_name: str) -> None:
    """Add ``channel_name`` through the ``n`` prompt of the open channel
    popup; the new entry ends up highlighted."""
    await pilot.press("n")
    await pilot.pause()
    await type_text(pilot, channel_name)
    await pilot.press("enter")
    await pilot.pause()


async def switch_channel(pilot: Pilot[None], channel_name: str) -> None:
    """Replace the single startup channel with ``channel_name``."""
    await open_channel_screen(pilot)
    await add_channel(pilot, channel_name)
    # The new channel is highlighted; the old one sits right above it.
    await pilot.press("k", "backspace", "enter")
    await wait_for_idle(pilot)


# --- package search -----------------------------------------------------------


def test_filter_types_shortcut_keys_into_search(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """While the search takes input, the keys bound to platform, channel,
    compare, quit, MatchSpec and who-needs are typed rather than triggered."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("slash", "p", "c", "C", "q", "m", "w")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_filter_backspace_and_slash(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Backspace`` edits the search and ``/`` is typed into it once the search
    is active."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("slash", "z", "x", "backspace", "slash", "backspace")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_filter_escape_restores_full_package_list(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Escape`` leaves the search and restores the full package list."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("slash")
        await type_text(pilot, "six")
        await wait_for_idle(pilot)
        await pilot.press("escape")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_filter_without_matches_shows_placeholder(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """A search without matches shows an empty list and a placeholder in the
    details."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("slash")
        await type_text(pilot, "numpy")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_filter_survives_opening_a_package(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """The footer no longer shows the search in the versions view, but going
    back returns to the filtered list."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("slash")
        await type_text(pilot, "zlib")
        await wait_for_idle(pilot)
        await pilot.press("enter")
        await wait_for_idle(pilot)
        await pilot.press("escape")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


# --- MatchSpec ----------------------------------------------------------------


def test_default_matchspec_opens_single_matching_package(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """A startup ``--matchspec`` that matches one package opens its versions."""

    assert snap_compare(
        make_app(
            default_matchspec=MatchSpec("libzlib >=1.3.2", exact_names_only=False)
        ),
        run_before=wait_for_idle,
        terminal_size=TERMINAL_SIZE,
    )


def test_matchspec_glob_lists_multiple_packages(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``*zlib`` matches ``libzlib`` and ``zlib``; the sidebar heading names the
    query."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_matchspec_query(pilot, "*zlib")

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_matchspec_without_matches(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """A MatchSpec without matches leaves the package list empty."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_matchspec_query(pilot, "numpy")

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_matchspec_from_versions_view_clears_search_filter(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``m`` works again once a package is open, and the result replaces the
    search filter that was active before."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("slash")
        await type_text(pilot, "six")
        await wait_for_idle(pilot)
        await pilot.press("enter")
        await wait_for_idle(pilot)
        await run_matchspec_query(pilot, "*zlib")

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_matchspec_screen_shows_inline_validation_error(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """An invalid MatchSpec is rejected with the parser's error shown inline."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("m")
        await pilot.pause()
        await type_text(pilot, "numpy[")
        await pilot.press("enter")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_matchspec_screen_reopens_with_previous_query_selected(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``m`` after a query reopens the prompt with the previous query selected."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_matchspec_query(pilot, "*zlib")
        await pilot.press("m")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_matchspec_screen_escape_keeps_result(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Escape`` in the MatchSpec prompt keeps the current query result."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_matchspec_query(pilot, "*zlib")
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("escape")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_empty_matchspec_restores_full_package_list(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Submitting an empty MatchSpec clears the query and shows every package
    again."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_matchspec_query(pilot, "*zlib")
        await pilot.press("m")
        await pilot.pause()
        # The previous query is selected, so backspace clears it.
        await pilot.press("backspace", "enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_enter_on_dependency_opens_matchspec_screen(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Selecting a dependency pre-fills the MatchSpec prompt with it."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await pilot.press("2", "enter")
        await wait_for_screen(pilot, MatchSpecScreen)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_dependency_matchspec_query_opens_dependency_versions(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``zlib`` pins an exact ``libzlib`` build (its last dependency);
    querying it opens exactly that build."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=4)
        await pilot.press("2", "G", "enter")
        await wait_for_screen(pilot, MatchSpecScreen)
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


# --- who needs ----------------------------------------------------------------


def test_whoneeds_screen_shows_inline_validation_error(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """A who-needs target that is not a package name is rejected inline."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("w")
        await pilot.pause()
        await type_text(pilot, "numpy >=2")
        await pilot.press("enter")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_whoneeds_screen_reopens_with_previous_target(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``w`` after a who-needs query reopens the prompt with the previous target."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_whoneeds_query(pilot, "libzlib")
        await pilot.press("w")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_whoneeds_without_dependents(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """A package nothing depends on yields an empty who-needs result."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_whoneeds_query(pilot, "six")

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_empty_whoneeds_restores_full_package_list(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Submitting an empty who-needs target clears the query and shows every
    package again."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_whoneeds_query(pilot, "libzlib")
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("ctrl+u", "enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_whoneeds_on_back_row_prefills_open_package(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Without a highlighted artifact, ``w`` asks for a name and offers the
    package that is open."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("j", "enter")
        await wait_for_idle(pilot)
        await pilot.press("w")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_whoneeds_on_artifact_asks_for_confirmation(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``w`` on a highlighted artifact asks to confirm the query for that exact
    build."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("w")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_whoneeds_confirmation_query_something_else_prefills_name(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Choosing *Query something else* opens the name prompt prefilled with the
    package."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_whoneeds_confirmation_cancel_keeps_versions_view(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Escape`` on the confirmation leaves the versions view untouched."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("escape")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_whoneeds_for_artifact_lists_its_exact_dependents(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``zlib`` depends on the exact ``libzlib 1.3.1`` builds, so the
    who-needs query for that build finds it. The ``libzlib 1.3.1`` builds
    themselves match through their run exports, the ``1.3.2`` builds do not."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=0)
        # Row 3 is libzlib 1.3.1 hb9d3cd8_2 (linux-64).
        await pilot.press("j")
        await wait_for_idle(pilot)
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


# --- platform selector --------------------------------------------------------


def test_platform_selector_space_toggles_platform(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Space`` unticks the highlighted platform and updates the selection count."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("p", "space")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_platform_selector_a_selects_all_platforms(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``a`` ticks every platform again after some were unticked."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("p", "space", "j", "space", "a")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_platform_selector_keeps_at_least_one_platform(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Unticking the last remaining platform is refused with a status message."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("p", "space", "j", "space", "j", "space")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_platform_selector_escape_discards_draft(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Escape`` discards unapplied platform changes; reopening shows the old
    selection."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("p", "space", "escape")
        await wait_for_idle(pilot)
        await pilot.press("p")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_applying_noarch_only_lists_noarch_packages(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Applying only ``noarch`` reloads the list with the noarch packages."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("p", "space", "j", "space", "enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_applying_unchanged_platforms_returns_to_packages(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Applying an unchanged selection just returns to the highlighted package."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await pilot.press("j", "p", "enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_default_platforms_restrict_the_startup_selection(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Unavailable platforms passed on the command line are dropped."""

    assert snap_compare(
        make_app(default_platforms=[Platform("osx-arm64"), Platform("win-64")]),
        run_before=wait_for_idle,
        terminal_size=TERMINAL_SIZE,
    )


def test_platform_change_reapplies_matchspec_query(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Changing platforms re-runs the active MatchSpec against the new selection."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_matchspec_query(pilot, "*zlib")
        await pilot.press("p", "j", "space", "enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_platform_change_reapplies_whoneeds_query(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Changing platforms re-runs the active who-needs query against the new
    selection."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_whoneeds_query(pilot, "libzlib")
        await pilot.press("p", "j", "space", "enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


# --- channels -----------------------------------------------------------------


def test_channel_screen_lists_selected_channels(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``c`` opens the channel popup listing the channels in priority order
    with the first one highlighted."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)

    assert snap_compare(
        make_app(default_channels=[MAIN_CHANNEL, BIOCONDA_CHANNEL]),
        run_before=run_before,
        terminal_size=TERMINAL_SIZE,
    )


def test_channel_screen_add_prompt_takes_input(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``n`` shows the input for a new channel; shortcut keys are typed into
    it rather than triggered."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)
        await pilot.press("n")
        await pilot.pause()
        await type_text(pilot, "prefix.dev/kn-q")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_channel_screen_adds_channel_at_the_end(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Submitting the prompt appends the channel and highlights it; nothing
    is loaded until the list is confirmed."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)
        await add_channel(pilot, BIOCONDA_CHANNEL)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_channel_screen_escape_in_prompt_returns_to_list(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Escape`` in the add prompt drops the typed name and goes back to the
    list without closing the popup."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)
        await pilot.press("n")
        await pilot.pause()
        await type_text(pilot, "bio")
        await pilot.press("escape")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_channel_screen_rejects_duplicate_channel(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Adding a channel that is already selected is refused."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)
        await add_channel(pilot, MAIN_CHANNEL)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_channel_screen_rejects_empty_channel(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Submitting a blank prompt is refused."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)
        await add_channel(pilot, "  ")

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_channel_screen_keeps_last_channel(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Backspace`` cannot remove the only remaining channel."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)
        await pilot.press("backspace")
        await pilot.pause()

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_channel_screen_removes_highlighted_channel(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``j`` moves down and ``Backspace`` removes the highlighted channel."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)
        await pilot.press("j", "backspace")
        await pilot.pause()

    assert snap_compare(
        make_app(default_channels=[MAIN_CHANNEL, BIOCONDA_CHANNEL]),
        run_before=run_before,
        terminal_size=TERMINAL_SIZE,
    )


def test_channel_screen_reorders_with_ctrl_j(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Ctrl+j`` moves the highlighted channel one position down."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)
        await pilot.press("ctrl+j")
        await pilot.pause()

    assert snap_compare(
        make_app(default_channels=[MAIN_CHANNEL, BIOCONDA_CHANNEL]),
        run_before=run_before,
        terminal_size=TERMINAL_SIZE,
    )


def test_channel_screen_reorders_with_ctrl_k(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Ctrl+k`` moves the highlighted channel one position up and stops at
    the top."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)
        await pilot.press("j", "ctrl+k", "ctrl+k")
        await pilot.pause()

    assert snap_compare(
        make_app(default_channels=[MAIN_CHANNEL, BIOCONDA_CHANNEL]),
        run_before=run_before,
        terminal_size=TERMINAL_SIZE,
    )


def test_channel_screen_escape_discards_edits(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``Escape`` closes the popup without applying; reopening it shows the
    channels that are actually loaded."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)
        await add_channel(pilot, BIOCONDA_CHANNEL)
        await pilot.press("escape")
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_confirming_unchanged_channels_keeps_the_view(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Confirming the popup without edits does not reload anything."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await open_channel_screen(pilot)
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_startup_channels_list_packages_of_every_channel(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Started with ``-c conda-forge -c bioconda`` the package list merges
    both channels."""

    assert snap_compare(
        make_app(default_channels=[MAIN_CHANNEL, BIOCONDA_CHANNEL]),
        run_before=wait_for_idle,
        terminal_size=TERMINAL_SIZE,
    )


def test_adding_channel_lists_packages_of_both_channels(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Adding ``bioconda`` next to ``conda-forge`` loads the packages of both
    channels."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await open_channel_screen(pilot)
        await add_channel(pilot, BIOCONDA_CHANNEL)
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_switching_channel_lists_its_packages(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Replacing ``conda-forge`` with ``bioconda`` loads that channel's
    packages and previews the first one."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await switch_channel(pilot, BIOCONDA_CHANNEL)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_switching_channel_clears_active_matchspec(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """Switching channels drops the active MatchSpec query along with the old
    channel."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_matchspec_query(pilot, "*zlib")
        await switch_channel(pilot, BIOCONDA_CHANNEL)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_switching_to_unreachable_channel_restores_previous_view(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """A channel without repodata fails to load; the previous view is restored
    with an error toast."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await switch_channel(pilot, MISSING_CHANNEL)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_adding_unreachable_channel_restores_previous_view(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """An unreachable channel is refused even next to a working one, so a
    typo does not silently browse the other channels; the toast names it."""

    async def run_before(pilot: Pilot[None]) -> None:
        await open_versions(pilot, package_index=1)
        await open_channel_screen(pilot)
        await add_channel(pilot, MISSING_CHANNEL)
        await pilot.press("enter")
        await wait_for_idle(pilot)

    assert snap_compare(make_app(), run_before=run_before, terminal_size=TERMINAL_SIZE)


def test_matchspec_query_spans_all_channels(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``p*`` matches ``pixi-browse`` and ``polars`` from conda-forge and
    ``pyfaidx`` from bioconda."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_matchspec_query(pilot, "p*")

    assert snap_compare(
        make_app(default_channels=[MAIN_CHANNEL, BIOCONDA_CHANNEL]),
        run_before=run_before,
        terminal_size=TERMINAL_SIZE,
    )


def test_whoneeds_query_spans_all_channels(
    snap_compare: SnapCompare, make_app: AppFactory
) -> None:
    """``pyfaidx`` (bioconda) depends on ``six`` (conda-forge); the reverse
    query crosses the channel boundary and opens the single match."""

    async def run_before(pilot: Pilot[None]) -> None:
        await wait_for_idle(pilot)
        await run_whoneeds_query(pilot, "six")

    assert snap_compare(
        make_app(default_channels=[MAIN_CHANNEL, BIOCONDA_CHANNEL]),
        run_before=run_before,
        terminal_size=TERMINAL_SIZE,
    )
