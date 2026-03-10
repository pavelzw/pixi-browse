from __future__ import annotations

from pathlib import Path

from rattler.networking import Client
from rattler.package_streaming import download_to_path as package_download_to_path


async def download_url_to_path(client: Client, url: str, destination: Path) -> None:
    await package_download_to_path(client, url, destination)
