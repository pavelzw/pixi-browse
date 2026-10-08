"""Read package archives of a channel, remote or local.

A channel given as a directory (``pixi-browse -c ./my-channel``) resolves to
records whose URL is a ``file://`` URL. Rattler's HTTP client cannot request
those: the scheme is rejected while the request is being built, so every read
fails with ``an error occurred during a range request: builder error for url
(file://...)``. Archives of a local channel are opened from their path
instead, which needs no range requests at all, and whole-archive downloads
become a file copy.
"""

from __future__ import annotations

import asyncio
import shutil
from os import PathLike
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from rattler.networking import Client
from rattler.package_streaming import (
    PackageArchive,
    download_to_path,
    fetch_raw_package_file_from_url,
)

__all__ = [
    "download_package_archive",
    "local_archive_path",
    "open_package_archive",
    "read_package_archive_file",
]


def local_archive_path(url: str) -> Path | None:
    """The file ``url`` names, or ``None`` when it is not a local archive.

    Only a ``file://`` URL for this machine has a path: one naming a host
    (``file://server/share/...``) is left to the client, which reports it as
    the unsupported request it is rather than reading some local file that
    happens to share the path.
    """
    parts = urlsplit(url)
    if parts.scheme != "file":
        return None
    if unquote(parts.netloc) not in ("", "localhost"):
        return None
    return Path(url2pathname(parts.path))


async def open_package_archive(client: Client, url: str) -> PackageArchive:
    """Open the archive at ``url`` for repeated reads."""
    path = local_archive_path(url)
    if path is not None:
        return await PackageArchive.from_path(path)
    return await PackageArchive.from_url(client, url)


async def read_package_archive_file(client: Client, url: str, file_path: str) -> bytes:
    """Read one file out of the archive at ``url``."""
    path = local_archive_path(url)
    if path is None:
        return await fetch_raw_package_file_from_url(client, url, file_path)
    contents = await (await PackageArchive.from_path(path)).read_file(file_path)
    if contents is None:
        raise FileNotFoundError(f"{path} does not contain {file_path}")
    return contents


async def download_package_archive(
    client: Client, url: str, destination: PathLike[str]
) -> None:
    """Write the archive at ``url`` to ``destination``."""
    path = local_archive_path(url)
    if path is None:
        await download_to_path(client, url, destination)
        return
    # Copying a large archive blocks long enough to stutter the TUI.
    await asyncio.to_thread(shutil.copyfile, path, destination)
