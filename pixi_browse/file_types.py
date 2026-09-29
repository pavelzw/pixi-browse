"""Name the type of a package file for the file action dialog.

Whether a file is text or binary follows the same deterministic rule as the
in-app preview. The optional ``filetype`` extra adds
[python-magic](https://github.com/ahupp/python-magic), whose libmagic
description names the type on top (``ELF 64-bit LSB shared object, x86-64,
...``, ``Python script``, ...). libmagic only ever feeds that description:
syntax highlighting keeps using the file's suffix and name.
"""

from __future__ import annotations

try:
    import magic
except ImportError:  # pragma: no cover - the extra, or libmagic itself, is missing
    MAGIC_AVAILABLE = False
else:
    # ``magic`` is also the import name of unrelated packages (``file-magic``,
    # ``magic``), which have no ``from_buffer``.
    MAGIC_AVAILABLE = hasattr(magic, "from_buffer")

__all__ = [
    "DETECTING_FILE_TYPE",
    "MAGIC_AVAILABLE",
    "decode_text_file",
    "describe_file_type",
]

# Stands in for the description while the file is still being fetched and read.
DETECTING_FILE_TYPE = "detecting…"

# Appended to the text/binary verdict when libmagic is not available. It names
# libmagic too: a PyPI install of the extra still needs the library itself.
MAGIC_INSTALL_HINT = "install pixi-browse[filetype] and libmagic to name the type"


def decode_text_file(content: bytes) -> str | None:
    """The file as text, or ``None`` when it is binary."""
    if b"\0" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def describe_file_type(content: bytes) -> str:
    """Describe ``content`` as ``text``/``binary``, followed by what libmagic
    says, e.g. ``binary · ELF 64-bit LSB shared object, x86-64, ...``.

    Runs libmagic, so call it from a thread. ``magic.from_buffer`` keeps the
    one libmagic handle it needs, behind a lock, so there is none to cache here.
    """
    kind = "text" if decode_text_file(content) is not None else "binary"
    if not MAGIC_AVAILABLE:
        return f"{kind} ({MAGIC_INSTALL_HINT})"
    try:
        description = magic.from_buffer(content)
    except magic.MagicException:
        # A libmagic error says nothing useful about the file; the text/binary
        # verdict stands on its own.
        return kind
    return f"{kind} · {description}"
