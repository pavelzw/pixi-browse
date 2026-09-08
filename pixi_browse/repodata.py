from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from rattler.exceptions import GatewayError
from rattler.match_spec import MatchSpec
from rattler.networking import Client
from rattler.platform import Platform
from rattler.repo_data import (
    Gateway,
    PackageRecord,
    RepoDataRecord,
    SourceConfig,
)

from pixi_browse.platform_utils import platform_sort_key

# The channel the app browses when none is given.
DEFAULT_CHANNEL = "conda-forge"
# The one subdir every conda channel must serve.
NOARCH_PLATFORM = Platform("noarch")


@dataclass(frozen=True)
class MatchSpecQueryResult:
    package_names: list[str]
    records_by_package: dict[str, list[RepoDataRecord]]


@dataclass(frozen=True)
class WhoNeedsQueryResult:
    package_names: list[str]
    records_by_package: dict[str, list[RepoDataRecord]]


def whoneeds_target_label(target: str | PackageRecord) -> str:
    if isinstance(target, str):
        return target
    return f"{target.name.normalized} {target.version} {target.build}"


def create_gateway(
    *,
    client: Client | None = None,
    cache_dir: Path | None = None,
) -> Gateway:
    return Gateway(
        cache_dir=cache_dir,
        default_config=SourceConfig(cache_action="cache-or-fetch"),
        client=client,
        show_progress=False,
    )


def normalize_channel_names(channel_names: Iterable[str]) -> list[str]:
    """Clean up a channel selection: strip, drop blanks and repeats, keep order."""
    normalized: list[str] = []
    for channel_name in channel_names:
        cleaned = channel_name.strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def channels_label(channel_names: Sequence[str]) -> str:
    """Render an ordered channel selection for status lines and titles."""
    return ", ".join(channel_names)


async def discover_available_platforms(
    *,
    gateway: Gateway,
    channel_names: Sequence[str],
    max_parallel: int = 12,
) -> list[Platform]:
    """Probe which platforms at least one of the channels serves repodata for.

    Every conda channel has to serve a ``noarch`` subdir, and rattler enforces
    that: a missing ``noarch`` is an error, while any other missing subdir is
    simply empty. A channel whose ``noarch`` cannot be fetched (a typo, a
    private channel without access) therefore cannot be browsed at all, and
    its ``GatewayError`` is raised instead of quietly browsing the other
    channels without it. Errors on the other subdirs only drop that platform.
    """
    candidates = sorted(
        Platform.all(),
        key=platform_sort_key,
    )
    semaphore = asyncio.Semaphore(max_parallel)

    async def probe(channel_name: str, platform: Platform) -> Platform | None:
        async with semaphore:
            try:
                names = await gateway.names(
                    sources=[channel_name],
                    platforms=[platform],
                )
            except GatewayError:
                if platform == NOARCH_PLATFORM:
                    raise
                return None

        return platform if names else None

    discovered = await asyncio.gather(
        *(
            probe(channel_name, platform)
            for channel_name in channel_names
            for platform in candidates
        )
    )
    return sorted(
        {platform for platform in discovered if platform is not None},
        key=platform_sort_key,
    )


async def fetch_package_names(
    *,
    gateway: Gateway,
    channel_names: Sequence[str],
    selected_platforms: Iterable[Platform],
) -> tuple[list[Platform], list[str]]:
    platforms = sorted(
        set(selected_platforms),
        key=platform_sort_key,
    )
    names = await gateway.names(
        sources=list(channel_names),
        platforms=platforms,
    )
    return platforms, sorted({name.normalized for name in names})


def record_identity_key(
    record: RepoDataRecord,
) -> tuple[str, str, str, int, str, str]:
    """Identify a record repeated across distinct who-needs dependency edges.

    Rattler returns one ``Dependent`` per matching dependency field, so the
    same record can occur more than once when it both depends on and constrains
    the target, for example. Pixi Browse displays records rather than edges and
    uses this key to collapse those occurrences.
    """
    return (
        record.name.normalized,
        str(record.version),
        record.build,
        record.build_number,
        record.subdir,
        record.file_name,
    )


async def query_package_records(
    *,
    gateway: Gateway,
    channel_names: Sequence[str],
    platforms: list[Platform],
    package_name: str,
) -> list[RepoDataRecord]:
    by_source = await gateway.query(
        sources=list(channel_names),
        platforms=platforms,
        specs=[package_name],
        recursive=False,
    )
    return sorted(
        (record for source_records in by_source for record in source_records),
        reverse=True,
    )


async def query_matchspec_records(
    *,
    gateway: Gateway,
    channel_names: Sequence[str],
    platforms: list[Platform],
    matchspec: MatchSpec,
) -> MatchSpecQueryResult:
    by_source = await gateway.query(
        sources=list(channel_names),
        platforms=platforms,
        specs=[matchspec],
        recursive=False,
    )
    grouped_records: dict[str, list[RepoDataRecord]] = {}
    for source_records in by_source:
        for record in source_records:
            package_name = record.name.normalized
            grouped_records.setdefault(package_name, []).append(record)

    sorted_package_names = sorted(grouped_records)
    return MatchSpecQueryResult(
        package_names=sorted_package_names,
        records_by_package={
            package_name: sorted(grouped_records[package_name], reverse=True)
            for package_name in sorted_package_names
        },
    )


async def query_whoneeds_records(
    *,
    gateway: Gateway,
    channel_names: Sequence[str],
    platforms: list[Platform],
    target: str | PackageRecord,
    log: Callable[[str], None],
) -> WhoNeedsQueryResult:
    """Return all records of the channels that depend on ``target``.

    The gateway performs the full repodata scan in Rust and only returns
    matching records to Python. Rattler always scans the complete repodata
    for this query, even on a gateway that otherwise browses sharded
    repodata, so the same gateway serves both kinds of queries.
    """
    target_label = whoneeds_target_label(target)
    platforms_label = ",".join(str(platform) for platform in platforms)
    log(
        "who-needs: starting gateway reverse query "
        f"target={target_label!r} channels={channels_label(channel_names)!r} "
        f"platforms={platforms_label!r}"
    )

    query_started = perf_counter()
    dependents = await gateway.who_needs(
        sources=list(channel_names),
        platforms=platforms,
        target=target,
    )
    query_duration = perf_counter() - query_started
    log(
        "who-needs: gateway reverse query finished "
        f"elapsed={query_duration:.3f}s matches={len(dependents):,}"
    )

    grouping_started = perf_counter()
    grouped_records: dict[
        str, dict[tuple[str, str, str, int, str, str], RepoDataRecord]
    ] = {}
    for dependent in dependents:
        record = dependent.record
        package_name = record.name.normalized
        grouped_records.setdefault(package_name, {})[record_identity_key(record)] = (
            record
        )
    grouping_duration = perf_counter() - grouping_started
    dependent_record_count = sum(len(records) for records in grouped_records.values())
    log(
        "who-needs: result grouping finished "
        f"elapsed={grouping_duration:.3f}s "
        f"unique_records={dependent_record_count:,} "
        f"packages={len(grouped_records):,}"
    )

    sorting_started = perf_counter()
    sorted_package_names = sorted(grouped_records)
    result = WhoNeedsQueryResult(
        package_names=sorted_package_names,
        records_by_package={
            package_name: sorted(
                grouped_records[package_name].values(),
                reverse=True,
            )
            for package_name in sorted_package_names
        },
    )
    sorting_duration = perf_counter() - sorting_started
    log(
        "who-needs: result sorting finished "
        f"elapsed={sorting_duration:.3f}s packages={len(sorted_package_names):,}"
    )
    return result
