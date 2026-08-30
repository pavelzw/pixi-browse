from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from rattler.platform import Platform
from rattler.version import Version


def platform_sort_key(platform: Platform) -> tuple[bool, str]:
    platform_name = str(platform)
    return (platform_name == "noarch", platform_name)


def sort_subdirs_by_latest_version[VersionedT](
    entries_by_subdir: Mapping[str, Sequence[VersionedT]],
    version: Callable[[VersionedT], Version],
) -> list[str]:
    """Sort subdirs by their latest package version, then by name."""
    subdirs = sorted(entries_by_subdir)
    subdirs.sort(
        key=lambda subdir: max(version(entry) for entry in entries_by_subdir[subdir]),
        reverse=True,
    )
    return subdirs
