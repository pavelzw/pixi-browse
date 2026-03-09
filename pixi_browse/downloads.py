from __future__ import annotations

from pathlib import Path

from rattler.networking import Client
from rattler.package_streaming import download


async def download_url_to_path(client: Client, url: str, destination: Path) -> None:
    await download(client, url, destination)
