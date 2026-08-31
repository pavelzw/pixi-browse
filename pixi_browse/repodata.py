from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import perf_counter

from rattler.exceptions import GatewayError
from rattler.match_spec import MatchSpec
from rattler.networking import Client
from rattler.platform import Platform
from rattler.repo_data import Gateway, PackageRecord, RepoDataRecord, SourceConfig
from rattler.version import VersionWithSource

from pixi_browse.platform_utils import platform_sort_key


@dataclass(frozen=True)
class MatchSpecQueryResult:
    package_names: list[str]
    records_by_package: dict[str, list[RepoDataRecord]]


@dataclass(frozen=True)
class WhoNeedsQueryResult:
    package_names: list[str]
    records_by_package: dict[str, list[RepoDataRecord]]


def create_gateway(
    *, client: Client | None = None, sharded_enabled: bool = True
) -> Gateway:
    return Gateway(
        default_config=SourceConfig(
            sharded_enabled=sharded_enabled,
            cache_action="cache-or-fetch",
        ),
        client=client,
        show_progress=False,
    )


async def discover_available_platforms(
    *,
    gateway: Gateway,
    channel_name: str,
    max_parallel: int = 12,
) -> list[Platform]:
    candidates = sorted(
        Platform.all(),
        key=platform_sort_key,
    )
    semaphore = asyncio.Semaphore(max_parallel)

    async def probe(platform: Platform) -> Platform | None:
        async with semaphore:
            try:
                names = await gateway.names(
                    sources=[channel_name],
                    platforms=[platform],
                )
            except GatewayError:
                return None

        return platform if names else None

    discovered = await asyncio.gather(*(probe(platform) for platform in candidates))
    return sorted(
        (platform for platform in discovered if platform is not None),
        key=platform_sort_key,
    )


async def fetch_package_names(
    *,
    gateway: Gateway,
    channel_name: str,
    selected_platforms: Iterable[Platform],
) -> tuple[list[Platform], list[str]]:
    platforms = sorted(
        set(selected_platforms),
        key=platform_sort_key,
    )
    names = await gateway.names(
        sources=[channel_name],
        platforms=platforms,
    )
    return platforms, sorted({name.normalized for name in names})


def record_identity_key(
    record: RepoDataRecord,
) -> tuple[str, str, str, int, str, str]:
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
    channel_name: str,
    platforms: list[Platform],
    package_name: str,
    record_sort_key: Callable[
        [RepoDataRecord], tuple[VersionWithSource, str, str, int]
    ],
) -> list[RepoDataRecord]:
    unique_records: dict[tuple[str, str, str, int, str, str], RepoDataRecord] = {}
    by_source = await gateway.query(
        sources=[channel_name],
        platforms=platforms,
        specs=[package_name],
        recursive=False,
    )
    for source_records in by_source:
        for record in source_records:
            unique_records[record_identity_key(record)] = record

    return sorted(
        unique_records.values(),
        key=record_sort_key,
        reverse=True,
    )


async def query_matchspec_records(
    *,
    gateway: Gateway,
    channel_name: str,
    platforms: list[Platform],
    matchspec: MatchSpec,
    record_sort_key: Callable[
        [RepoDataRecord], tuple[VersionWithSource, str, str, int]
    ],
) -> MatchSpecQueryResult:
    unique_records: dict[tuple[str, str, str, int, str, str], RepoDataRecord] = {}
    by_source = await gateway.query(
        sources=[channel_name],
        platforms=platforms,
        specs=[matchspec],
        recursive=False,
    )
    for source_records in by_source:
        for record in source_records:
            unique_records[record_identity_key(record)] = record

    grouped_records: dict[str, list[RepoDataRecord]] = {}
    for record in unique_records.values():
        package_name = record.name.normalized
        grouped_records.setdefault(package_name, []).append(record)

    sorted_package_names = sorted(grouped_records)
    return MatchSpecQueryResult(
        package_names=sorted_package_names,
        records_by_package={
            package_name: sorted(
                grouped_records[package_name],
                key=record_sort_key,
                reverse=True,
            )
            for package_name in sorted_package_names
        },
    )


async def query_whoneeds_records(
    *,
    gateway: Gateway,
    channel_name: str,
    platforms: list[Platform],
    target: str | PackageRecord,
    record_sort_key: Callable[
        [RepoDataRecord], tuple[VersionWithSource, str, str, int]
    ],
    log: Callable[[str], None] | None = None,
) -> WhoNeedsQueryResult:
    """Return all channel records that depend on ``target``.

    The gateway performs the full repodata scan in Rust and only returns
    matching records to Python. Callers must pass a gateway configured with
    sharded repodata disabled so the scan does not fetch every package shard.
    """
    target_label = (
        target
        if isinstance(target, str)
        else f"{target.name.normalized} {target.version} {target.build}"
    )
    platforms_label = ",".join(str(platform) for platform in platforms)
    if log is not None:
        log(
            "who-needs: starting gateway reverse query "
            f"target={target_label!r} channel={channel_name!r} "
            f"platforms={platforms_label!r}"
        )

    query_started = perf_counter()
    dependents = await gateway.who_needs(
        sources=[channel_name],
        platforms=platforms,
        target=target,
    )
    query_duration = perf_counter() - query_started
    if log is not None:
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
    if log is not None:
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
                key=record_sort_key,
                reverse=True,
            )
            for package_name in sorted_package_names
        },
    )
    sorting_duration = perf_counter() - sorting_started
    if log is not None:
        log(
            "who-needs: result sorting finished "
            f"elapsed={sorting_duration:.3f}s packages={len(sorted_package_names):,}"
        )
    return result
