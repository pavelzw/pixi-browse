from __future__ import annotations

import textwrap
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

from rich.markup import escape


def format_detail_row(label: str, value: str) -> str:
    return f"{label:<20}{value}"


def format_clickable_url(url: str) -> str:
    return f"[@click=app.open_external_url({url!r})]{escape(url)}[/]"


def format_clickable_url_list(label: str, urls: Sequence[str] | None) -> list[str]:
    if not urls:
        return []
    return [f"{label} " + ", ".join(format_clickable_url(url) for url in urls)]


def format_record_value(value: Any) -> str:
    if value is None:
        return "not available"
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, list):
        if not value:
            return "none"
        return ", ".join(str(item) for item in value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except TypeError:
            pass
    text = str(value)
    if text == "NoArchType(None)":
        return "none"
    return escape(text)


def format_byte_size(value: Any) -> str:
    if value is None:
        return "not available"
    if not isinstance(value, int) or value < 0:
        return format_record_value(value)

    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    size = float(value)
    unit = units[0]
    for candidate in units:
        unit = candidate
        if size < 1024.0 or candidate == units[-1]:
            break
        size /= 1024.0

    if unit == "B":
        return f"{value:,} B"
    return f"{size:.1f} {unit} ({value:,} bytes)"


def render_kv_box(rows: list[tuple[str, str]], width: int) -> list[str]:
    if not rows:
        return []
    label_width = max(len(label) for label, _ in rows)
    inner_width = max(30, width - 2)
    value_width = max(10, inner_width - label_width - 3)

    lines = ["╭" + ("─" * inner_width) + "╮"]
    for label, value in rows:
        wrapped = textwrap.wrap(value, width=value_width) or [""]
        lines.append(f"│ {label:<{label_width}} {wrapped[0]:<{value_width}} │")
        for continuation in wrapped[1:]:
            lines.append(f"│ {'':<{label_width}} {continuation:<{value_width}} │")
    lines.append("╰" + ("─" * inner_width) + "╯")
    return lines


def render_package_preview(
    package_name: str,
    records: list[Any],
    *,
    record_sort_key: Callable[[Any], tuple[Any, str, str, int]],
) -> str:
    if not records:
        return f"# {package_name}\n\nNo metadata records found."

    grouped_by_subdir: dict[str, list[Any]] = defaultdict(list)
    for record in records:
        grouped_by_subdir[record.subdir].append(record)

    for subdir_records in grouped_by_subdir.values():
        subdir_records.sort(
            key=lambda record: (
                *record_sort_key(record),
                record.file_name,
            ),
            reverse=True,
        )

    sorted_subdirs = sorted(
        grouped_by_subdir,
        key=lambda subdir: (subdir == "noarch", subdir),
    )

    version_width = max(len(str(record.version)) for record in records)
    build_width = max(len(record.build) for record in records)

    lines = [
        f"# {escape(package_name)}",
        "",
        f"Version selector preview ({len(records)} artifact{'s' if len(records) != 1 else ''}):",
        "Press Enter to open the version list.",
    ]

    for subdir in sorted_subdirs:
        subdir_records = grouped_by_subdir[subdir]
        lines.extend(
            [
                "",
                f"▾ {escape(subdir)} ({len(subdir_records)})",
            ]
        )
        for record in subdir_records:
            lines.append(
                f"{escape(str(record.version)):<{version_width}} "
                f"{escape(record.build):<{build_width}}"
            )

    return "\n".join(lines)


def render_selected_version_details(
    package_name: str,
    record: Any,
    *,
    content_width: int,
    package_paths: Sequence[str] | None = None,
    package_paths_error: str | None = None,
    repository_urls: Sequence[str] | None = None,
    documentation_urls: Sequence[str] | None = None,
    homepage_urls: Sequence[str] | None = None,
) -> str:
    name_value = (
        record.name.source if hasattr(record.name, "source") else str(record.name)
    )
    table_rows: list[tuple[str, str]] = [
        ("Name", name_value),
        ("Version", format_record_value(record.version)),
        ("Build", format_record_value(record.build)),
        ("Build Number", format_record_value(record.build_number)),
        ("Subdir", format_record_value(record.subdir)),
        ("File Name", format_record_value(record.file_name)),
        ("Channel", format_record_value(record.channel)),
        ("Size", format_byte_size(record.size)),
        ("Timestamp", format_record_value(record.timestamp)),
        ("License", format_record_value(record.license)),
        ("License Family", format_record_value(record.license_family)),
        ("Arch", format_record_value(record.arch)),
        ("Platform", format_record_value(record.platform)),
        ("NoArch", format_record_value(record.noarch)),
        ("Features", format_record_value(record.features)),
        ("Track Features", format_record_value(record.track_features)),
        (
            "Python Site-Packages",
            format_record_value(record.python_site_packages_path),
        ),
        ("MD5", format_record_value(record.md5)),
        ("SHA256", format_record_value(record.sha256)),
        ("Legacy .tar.bz2 MD5", format_record_value(record.legacy_bz2_md5)),
        ("Legacy .tar.bz2 Size", format_byte_size(record.legacy_bz2_size)),
    ]

    dependencies = (
        ["Dependencies:"]
        + [
            f" - {escape(str(dependency))}"
            for dependency in (record.depends if record.depends else [])
        ]
        if record.depends
        else ["Dependencies: none"]
    )
    constrains = (
        ["Constrains:"]
        + [
            f" - {escape(str(constraint))}"
            for constraint in (record.constrains if record.constrains else [])
        ]
        if record.constrains
        else ["Constrains: none"]
    )
    url = str(record.url)

    lines = [
        f"# {escape(package_name)} {escape(str(record.version))}",
        "",
        "Repodata metadata:",
    ]
    lines.extend(render_kv_box(table_rows, content_width))
    repository_lines = format_clickable_url_list("Repository:", repository_urls)
    documentation_lines = format_clickable_url_list(
        "Documentation:", documentation_urls
    )
    homepage_lines = format_clickable_url_list("Homepage:", homepage_urls)
    if package_paths_error is not None:
        file_lines = [
            "Files:",
            f" - unavailable: {escape(package_paths_error)}",
        ]
    elif package_paths:
        file_lines = [
            "Files:",
            *[f" - {escape(path)}" for path in package_paths],
        ]
    else:
        file_lines = ["Files: none"]

    lines.extend(
        [
            "",
            f"URL: {format_clickable_url(url)}",
            *([""] + repository_lines if repository_lines else []),
            *([""] + documentation_lines if documentation_lines else []),
            *([""] + homepage_lines if homepage_lines else []),
            "",
            *dependencies,
            "",
            *constrains,
            "",
            *file_lines,
        ]
    )
    return "\n".join(lines)
