from __future__ import annotations

import textwrap
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from rich.markup import escape


def format_detail_row(label: str, value: str) -> str:
    return f"{label:<20}{value}"


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

    latest = records[0]
    latest_version = str(latest.version)
    latest_version_records = [
        record for record in records if str(record.version) == latest_version
    ]
    latest_extra_builds = max(0, len(latest_version_records) - 1)
    latest_extra_text = (
        f" (+ {latest_extra_builds} build{'s' if latest_extra_builds != 1 else ''})"
        if latest_extra_builds
        else ""
    )

    license_name = latest.license if latest.license is not None else "unknown"
    size_text = format_byte_size(latest.size)
    md5_text = latest.md5.hex() if latest.md5 is not None else "not available"
    sha256_text = latest.sha256.hex() if latest.sha256 is not None else "not available"

    grouped_by_version: dict[str, list[Any]] = defaultdict(list)
    for record in records:
        grouped_by_version[str(record.version)].append(record)

    for version_records in grouped_by_version.values():
        version_records.sort(key=record_sort_key, reverse=True)

    sorted_versions = sorted(
        grouped_by_version.items(),
        key=lambda item: record_sort_key(item[1][0]),
        reverse=True,
    )
    other_versions = [
        (version, version_records)
        for version, version_records in sorted_versions
        if version != latest_version
    ]

    name_value = (
        latest.name.source if hasattr(latest.name, "source") else str(latest.name)
    )
    artifact_line = f"{latest.file_name}{latest_extra_text}"
    divider = "-" * max(44, len(artifact_line))

    lines = [
        f"# {package_name}",
        "",
        artifact_line,
        divider,
        "",
        format_detail_row("Name", name_value),
        format_detail_row("Version", latest_version),
        format_detail_row("Build", latest.build),
        format_detail_row("Size", size_text),
        format_detail_row("License", license_name),
        format_detail_row("Subdir", latest.subdir),
        format_detail_row("File Name", latest.file_name),
        format_detail_row("URL", str(latest.url)),
        format_detail_row("MD5", md5_text),
        format_detail_row("SHA256", sha256_text),
        "",
        "Dependencies:",
    ]

    if latest.depends:
        lines.extend(f" - {dependency}" for dependency in latest.depends)
    else:
        lines.append(" - none")

    lines.extend(
        [
            "",
            "Run exports: not available in repodata",
            "",
            f"Other Versions ({len(other_versions)}):",
        ]
    )

    if other_versions:
        lines.append("Version    Build")
        preview_count = 4
        for version, version_records in other_versions[:preview_count]:
            best = version_records[0]
            extra_builds = max(0, len(version_records) - 1)
            extra_text = (
                f"  (+ {extra_builds} build{'s' if extra_builds != 1 else ''})"
                if extra_builds
                else ""
            )
            lines.append(f"{version:<10} {best.build}{extra_text}")

        remaining = len(other_versions) - preview_count
        if remaining > 0:
            lines.append(f"... and {remaining} more")

    return "\n".join(lines)


def render_selected_version_details(
    package_name: str,
    record: Any,
    *,
    content_width: int,
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

    dependencies = [
        f" - {escape(str(dependency))}"
        for dependency in (record.depends if record.depends else [])
    ] or [" - none"]
    constrains = [
        f" - {escape(str(constraint))}"
        for constraint in (record.constrains if record.constrains else [])
    ] or [" - none"]
    url = str(record.url)
    escaped_url = escape(url)
    link_target = escaped_url.replace('"', '\\"')

    lines = [
        f"# {escape(package_name)} {escape(str(record.version))}",
        "",
        "Repodata metadata:",
    ]
    lines.extend(render_kv_box(table_rows, content_width))
    lines.extend(
        [
            "",
            "URL:",
            f'[link="{link_target}"]{escaped_url}[/link]',
            "",
            "Dependencies:",
            *dependencies,
            "",
            "Constrains:",
            *constrains,
            "",
            "Files:",
            " - placeholder: coming soon",
        ]
    )
    return "\n".join(lines)
