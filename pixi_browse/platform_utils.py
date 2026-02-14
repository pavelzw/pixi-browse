from __future__ import annotations

from rattler.platform import Platform


def platform_sort_key(platform: Platform) -> tuple[bool, str]:
    platform_name = str(platform)
    return (platform_name == "noarch", platform_name)
