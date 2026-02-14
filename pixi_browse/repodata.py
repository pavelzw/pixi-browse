from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from rattler.exceptions import GatewayError
from rattler.platform import Platform
from rattler.repo_data import Gateway, SourceConfig

from pixi_browse.platform_utils import platform_sort_key


def create_gateway(*, cache_dir: Path) -> Gateway:
    return Gateway(
        cache_dir=cache_dir,
        default_config=SourceConfig(
            sharded_enabled=True,
            cache_action="cache-or-fetch",
        ),
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


def record_identity_key(record: Any) -> tuple[str, str, int, str, str]:
    return (
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
    record_sort_key: Callable[[Any], tuple[Any, str, str, int]],
) -> list[Any]:
    unique_records: dict[tuple[str, str, int, str, str], Any] = {}
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
