"""Name the type of a package file for the file action dialog.

Whether a file is text or binary follows the same deterministic rule as the
in-app preview. The optional ``filetype`` extra adds
[Magika](https://github.com/google/magika), whose model names the type on top
(``ELF executable``, ``Python source``, ...). Magika only ever feeds that
description: syntax highlighting keeps using the file's suffix and name.
"""

from __future__ import annotations

import threading

try:
    from magika import Magika
except ImportError:  # pragma: no cover - depends on the optional ``filetype`` extra
    MAGIKA_AVAILABLE = False
else:
    MAGIKA_AVAILABLE = True

__all__ = [
    "MAGIKA_AVAILABLE",
    "decode_text_file",
    "describe_file_type",
]

# Appended to the text/binary verdict when Magika is not installed.
MAGIKA_INSTALL_HINT = "install pixi-browse[filetype] to name the type"

_magika: Magika | None = None
_magika_lock = threading.Lock()


def decode_text_file(content: bytes) -> str | None:
    """The file as text, or ``None`` when it is binary."""
    if b"\0" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _get_magika() -> Magika:
    # Loading the model takes a noticeable fraction of a second, so it is
    # loaded once, on the first file that is described.
    global _magika
    with _magika_lock:
        if _magika is None:
            _magika = Magika()
        return _magika


def describe_file_type(content: bytes) -> str:
    """Describe ``content`` as ``text``/``binary``, followed by the type Magika
    names, e.g. ``binary · ELF executable (application/x-executable-elf)``.

    Loads and runs the Magika model, so call it from a thread.
    """
    kind = "text" if decode_text_file(content) is not None else "binary"
    if not MAGIKA_AVAILABLE:
        return f"{kind} ({MAGIKA_INSTALL_HINT})"
    result = _get_magika().identify_bytes(content)
    if not result.ok:
        return kind
    output = result.output
    return f"{kind} · {output.description} ({output.mime_type})"
