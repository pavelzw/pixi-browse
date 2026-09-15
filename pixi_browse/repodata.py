from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from rattler.config import Config
from rattler.exceptions import GatewayError
from rattler.match_spec import MatchSpec
from rattler.networking import Client
from rattler.platform import Platform
from rattler.repo_data import (
    Gateway,
    PackageRecord,
    RepoDataRecord,
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


@dataclass(frozen=True)
class PlatformDiscoveryProgress:
    """How far ``discover_available_platforms`` has come.

    A probe is one channel asked for one subdir. ``found`` and ``checking``
    span all channels: a platform is found once any channel serves it and is
    being checked while any channel's probe for it is still running.
    """

    probes_total: int
    probes_completed: int
    found: tuple[Platform, ...]
    checking: tuple[Platform, ...]


DiscoveryProgressCallback = Callable[[PlatformDiscoveryProgress], None]


def whoneeds_target_label(target: str | PackageRecord) -> str:
    if isinstance(target, str):
        return target
    return f"{target.name.normalized} {target.version} {target.build}"


def create_gateway(
    *,
    client: Client | None = None,
    config: Config | None = None,
    cache_dir: Path | None = None,
) -> Gateway:
    config = config if config is not None else Config()
    return Gateway.from_config(
        config,
        cache_dir=cache_dir,
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
    on_progress: DiscoveryProgressCallback | None = None,
) -> list[Platform]:
    """Probe which platforms at least one of the channels serves repodata for.

    Every conda channel has to serve a ``noarch`` subdir, and rattler enforces
    that: a missing ``noarch`` is an error, while any other missing subdir is
    simply empty. A channel whose ``noarch`` cannot be fetched (a typo, a
    private channel without access) therefore cannot be browsed at all, and
    its ``GatewayError`` is raised instead of quietly browsing the other
    channels without it. Errors on the other subdirs only drop that platform.

    A platform counts once any channel lists a package for it: rattler answers
    a subdir the channel does not have with an empty list too, so a missing
    subdir and an empty one cannot be told apart. When no platform lists a
    package at all, the channels are reachable (their ``noarch`` probes
    succeeded) but empty, and ``noarch`` is returned as the one subdir they
    are known to serve, so that an empty channel opens with an empty package
    list rather than failing to load.

    ``on_progress`` is called whenever a probe starts or finishes. Without
    sharded repodata a probe downloads and parses the subdir's complete
    ``repodata.json``, so this is where a slow start spends its time.
    """
    candidates = sorted(
        Platform.all(),
        key=platform_sort_key,
    )
    semaphore = asyncio.Semaphore(max_parallel)
    probes_total = len(candidates) * len(channel_names)
    probes_completed = 0
    found: set[Platform] = set()
    # Platforms with a running probe, and how many channels are probing them.
    checking: dict[Platform, int] = {}

    def report() -> None:
        if on_progress is None:
            return
        on_progress(
            PlatformDiscoveryProgress(
                probes_total=probes_total,
                probes_completed=probes_completed,
                found=tuple(sorted(found, key=platform_sort_key)),
                checking=tuple(sorted(checking, key=platform_sort_key)),
            )
        )

    def finish_probe(platform: Platform, *, serves_repodata: bool) -> None:
        nonlocal probes_completed
        probes_completed += 1
        checking[platform] -= 1
        if checking[platform] == 0:
            del checking[platform]
        if serves_repodata:
            found.add(platform)
        report()

    async def probe(channel_name: str, platform: Platform) -> Platform | None:
        async with semaphore:
            checking[platform] = checking.get(platform, 0) + 1
            report()
            try:
                names = await gateway.names(
                    sources=[channel_name],
                    platforms=[platform],
                )
            except GatewayError:
                finish_probe(platform, serves_repodata=False)
                if platform == NOARCH_PLATFORM:
                    raise
                return None
            finish_probe(platform, serves_repodata=bool(names))

        return platform if names else None

    discovered = await asyncio.gather(
        *(
            probe(channel_name, platform)
            for channel_name in channel_names
            for platform in candidates
        )
    )
    available = {platform for platform in discovered if platform is not None}
    if not available:
        return [NOARCH_PLATFORM]
    return sorted(available, key=platform_sort_key)


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
