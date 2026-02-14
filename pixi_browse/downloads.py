from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path


def download_url_to_path(
    url: str, destination: Path, *, timeout_seconds: float
) -> None:
    with (
        urllib.request.urlopen(  # noqa: S310
            url,
            timeout=timeout_seconds,
        ) as response,
        destination.open("wb") as handle,
    ):
        shutil.copyfileobj(response, handle)
