"""Benchmark the py-rattler operations used by pixi-browse's who-needs view.

Example:
    pixi run python scripts/benchmark_whoneeds.py python -p linux-64 -p noarch
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from collections.abc import Sequence
from time import perf_counter
from typing import cast

from rattler.match_spec import MatchSpec
from rattler.networking import Client
from rattler.package import PackageName
from rattler.platform import Platform
from rattler.repo_data import (
    Dependent,
    Gateway,
    PackageRecord,
    RepoDataRecord,
    SourceConfig,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Gateway.who_needs queries as used by pixi-browse."
    )
    parser.add_argument("package", nargs="?", default="python")
    parser.add_argument("-c", "--channel", default="conda-forge")
    parser.add_argument(
        "-p",
        "--platform",
        action="append",
        dest="platforms",
        help="repeat for multiple platforms; default: current platform and noarch",
    )
    parser.add_argument("-n", "--iterations", type=int, default=5)
    return parser.parse_args()


def selected_platforms(values: Sequence[str] | None) -> list[Platform]:
    selected = (
        (Platform(value) for value in values)
        if values
        else (Platform.current(), Platform("noarch"))
    )
    return list(dict.fromkeys(selected))


def record_identity(record: RepoDataRecord) -> tuple[str, str, str]:
    return (record.channel, record.subdir, record.file_name)


def unique_records(
    records_by_source: Sequence[Sequence[RepoDataRecord]],
) -> list[RepoDataRecord]:
    records: dict[tuple[str, str, str], RepoDataRecord] = {}
    for source_records in records_by_source:
        for record in source_records:
            records[record_identity(record)] = record
    return list(records.values())


def concrete_target(
    records: Sequence[RepoDataRecord], package_name: PackageName
) -> RepoDataRecord | None:
    candidates = [record for record in records if record.name == package_name]
    return (
        max(
            candidates,
            key=lambda record: (
                record.version,
                record.build_number,
                record.build,
                record.subdir,
            ),
        )
        if candidates
        else None
    )


def duration(seconds: float) -> str:
    return f"{seconds * 1_000:.1f} ms" if seconds < 1 else f"{seconds:.3f} s"


async def benchmark(
    gateway: Gateway,
    channel: str,
    platforms: list[Platform],
    target: str | PackageRecord,
    iterations: int,
) -> tuple[list[float], list[Dependent]]:
    durations: list[float] = []
    dependents: list[Dependent] = []
    for _ in range(iterations):
        started = perf_counter()
        dependents = await gateway.who_needs(
            sources=[channel],
            platforms=platforms,
            target=target,
        )
        durations.append(perf_counter() - started)
    return durations, dependents


def print_result(
    label: str, durations: list[float], dependents: list[Dependent]
) -> None:
    started = perf_counter()
    grouped: dict[str, dict[tuple[str, str, str], RepoDataRecord]] = {}
    for dependent in dependents:
        record = dependent.record
        grouped.setdefault(record.name.normalized, {})[record_identity(record)] = record
    for records in grouped.values():
        sorted(
            records.values(),
            key=lambda record: (
                record.version,
                record.build,
                record.subdir,
                record.build_number,
            ),
            reverse=True,
        )
    grouping_duration = perf_counter() - started
    record_count = sum(len(records) for records in grouped.values())

    print(
        f"{label}: {len(dependents):,} matches, {record_count:,} records, "
        f"{len(grouped):,} package names"
    )
    print(
        f"  min {duration(min(durations))}, "
        f"median {duration(statistics.median(durations))}, "
        f"mean {duration(statistics.mean(durations))}, "
        f"max {duration(max(durations))}"
    )
    print(f"  calls: {', '.join(duration(value) for value in durations)}")
    print(f"  pixi-browse-style grouping/sorting: {duration(grouping_duration)}")


async def query_target_records(
    gateway: Gateway,
    channel: str,
    platforms: list[Platform],
    package: PackageName,
) -> list[RepoDataRecord]:
    records_by_source = await gateway.query(
        sources=[channel],
        platforms=platforms,
        specs=[MatchSpec(package.normalized)],
        recursive=False,
    )
    return unique_records(records_by_source)


async def main() -> None:
    args = parse_args()
    package = PackageName(cast(str, args.package))
    channel = cast(str, args.channel)
    platforms = selected_platforms(cast(list[str] | None, args.platforms))
    iterations = cast(int, args.iterations)
    if iterations < 1:
        raise SystemExit("iteration count must be at least 1")

    print(f"Channel: {channel}")
    print(f"Platforms: {', '.join(str(platform) for platform in platforms)}")
    print(f"Package: {package.normalized}")
    gateway = Gateway(
        default_config=SourceConfig(
            sharded_enabled=False,
            cache_action="cache-or-fetch",
        ),
        client=Client.default_client(user_agent="pixi-browse-whoneeds-benchmark"),
        show_progress=False,
    )
    print_result(
        "Name target",
        *await benchmark(
            gateway,
            channel,
            platforms,
            package.normalized,
            iterations,
        ),
    )
    records = await query_target_records(gateway, channel, platforms, package)
    exact = concrete_target(records, package)
    if exact is None:
        print("Exact target skipped: package not found")
        return
    print(
        f"Exact target: {exact.name.normalized} {exact.version} {exact.build} [{exact.subdir}]"
    )
    print_result(
        "Exact record target",
        *await benchmark(
            gateway,
            channel,
            platforms,
            exact,
            iterations,
        ),
    )


if __name__ == "__main__":
    asyncio.run(main())
