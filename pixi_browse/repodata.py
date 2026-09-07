from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import perf_counter

from rattler.exceptions import GatewayError
from rattler.match_spec import MatchSpec
from rattler.networking import Client
from rattler.platform import Platform
from rattler.repo_data import (
    Gateway,
    PackageFormatSelection,
    PackageRecord,
    RepoDataRecord,
    RepodataRevisionMetadata,
    SourceConfig,
)

from pixi_browse.platform_utils import platform_sort_key

RepodataRevisions = dict[str, RepodataRevisionMetadata]
"""Revisions advertised by one subdirectory, keyed by ``vN`` as in CEP 48."""

SUPPORTED_REPODATA_REVISION = 3
"""The newest CEP 48 repodata revision that rattler can read."""


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
    *, client: Client | None = None, sharded_enabled: bool = True
) -> Gateway:
    return Gateway(
        default_config=SourceConfig(
            sharded_enabled=sharded_enabled,
            cache_action="cache-or-fetch",
            # Pixi Browse inspects conda archives only. Selecting the format
            # explicitly keeps sharded and full repodata in agreement and
            # keeps CEP 48 `.whl` records out of the version list.
            package_format_selection=PackageFormatSelection.PREFER_CONDA,
        ),
        client=client,
        show_progress=False,
    )


async def fetch_repodata_revisions(
    *,
    gateway: Gateway,
    channel_name: str,
    platforms: Iterable[Platform],
) -> dict[Platform, RepodataRevisions]:
    """Return the CEP 48 repodata revisions each platform of ``channel_name`` advertises.

    The gateway answers from the subdirectories it already fetched for the
    package names, so this does not cost another request.
    """
    sorted_platforms = sorted(set(platforms), key=platform_sort_key)
    revisions = await asyncio.gather(
        *(
            gateway.repodata_revisions(channel_name, platform)
            for platform in sorted_platforms
        )
    )
    return dict(zip(sorted_platforms, revisions, strict=True))


def repodata_revision_number(revision: str) -> int | None:
    """Parse the ``vN`` key of a repodata revision into ``N``."""
    if not revision.startswith(("v", "V")):
        return None
    try:
        return int(revision[1:])
    except ValueError:
        return None


def format_repodata_revisions_summary(
    revisions_by_platform: dict[Platform, RepodataRevisions],
) -> str | None:
    """Summarize the repodata revisions advertised across the selected platforms.

    Returns ``None`` for channels that only publish legacy repodata. Package
    counts are summed across platforms when every platform reports one, and
    revisions newer than rattler understands are marked as unsupported.
    """
    package_totals: dict[str, int | None] = {}
    for revisions in revisions_by_platform.values():
        for revision, metadata in revisions.items():
            n_packages = metadata.get("n_packages")
            if revision not in package_totals:
                package_totals[revision] = n_packages
                continue
            total = package_totals[revision]
            package_totals[revision] = (
                total + n_packages
                if total is not None and n_packages is not None
                else None
            )
    if not package_totals:
        return None

    def sort_key(revision: str) -> tuple[bool, int, str]:
        number = repodata_revision_number(revision)
        return (number is None, number or 0, revision)

    parts: list[str] = []
    for revision in sorted(package_totals, key=sort_key):
        label = revision
        n_packages = package_totals[revision]
        if n_packages is not None:
            label += f" ({n_packages:,} package{'s' if n_packages != 1 else ''})"
        number = repodata_revision_number(revision)
        if number is None or number > SUPPORTED_REPODATA_REVISION:
            label += " [unsupported]"
        parts.append(label)
    return "Repodata revisions: " + ", ".join(parts)


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
    channel_name: str,
    platforms: list[Platform],
    package_name: str,
) -> list[RepoDataRecord]:
    by_source = await gateway.query(
        sources=[channel_name],
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
    channel_name: str,
    platforms: list[Platform],
    matchspec: MatchSpec,
) -> MatchSpecQueryResult:
    by_source = await gateway.query(
        sources=[channel_name],
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
    channel_name: str,
    platforms: list[Platform],
    target: str | PackageRecord,
    log: Callable[[str], None],
) -> WhoNeedsQueryResult:
    """Return all channel records that depend on ``target``.

    The gateway performs the full repodata scan in Rust and only returns
    matching records to Python. Callers should pass a gateway configured with
    sharded repodata disabled: against sharded repodata the scan fetches one
    shard per package name, while the full repodata is a single request.
    """
    target_label = whoneeds_target_label(target)
    platforms_label = ",".join(str(platform) for platform in platforms)
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
