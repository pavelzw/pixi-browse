from __future__ import annotations

import textwrap
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlparse

from rattler.package import RunExportsJson
from rattler.repo_data import RepoDataRecord
from rattler.version import VersionWithSource
from rich.markup import escape

from pixi_browse.models import VersionDetailsData


def format_detail_row(label: str, value: str) -> str:
    return f"{label:<20}{value}"


def format_detail_rows(rows: Sequence[tuple[str, str]]) -> list[str]:
    if not rows:
        return []
    label_width = max(len(label) for label, _ in rows)
    return [f"{label:<{label_width}}  {value}" for label, value in rows]


def format_clickable_url(url: str) -> str:
    return format_clickable_link(escape(url), url)


def format_clickable_link(label: str, url: str) -> str:
    return f"[@click=app.open_external_url({url!r})]{label}[/]"


def format_clickable_url_list(label: str, urls: Sequence[str] | None) -> list[str]:
    if not urls:
        return []
    return [f"{label} " + ", ".join(format_clickable_url(url) for url in urls)]


def format_clickable_github_handle(handle: str) -> str:
    normalized = handle.lstrip("@")
    return format_clickable_link(
        f"@{escape(normalized)}",
        f"https://github.com/{normalized}",
    )


def format_clickable_github_handle_list(
    label: str, handles: Sequence[str] | None
) -> list[str]:
    if not handles:
        return []
    return [
        f"{label} "
        + ", ".join(format_clickable_github_handle(handle) for handle in handles)
    ]


def format_provenance(remote_url: str | None, sha: str | None) -> list[str]:
    if not remote_url or not sha:
        return []

    parsed = urlparse(remote_url)
    path_parts = [part for part in parsed.path.removesuffix(".git").split("/") if part]
    if parsed.netloc == "github.com" and len(path_parts) >= 2:
        slug = "/".join(path_parts[:2])
        commit_url = f"https://github.com/{slug}/commit/{sha}"
        label = f"{slug}@{sha}"
    else:
        commit_url = remote_url
        label = f"{remote_url}@{sha}"

    return [f"Provenance: {format_clickable_link(escape(label), commit_url)}"]


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
    records: list[RepoDataRecord],
    *,
    record_sort_key: Callable[
        [RepoDataRecord], tuple[VersionWithSource, str, str, int]
    ],
) -> str:
    if not records:
        return f"# {package_name}\n\nNo metadata records found."

    grouped_by_subdir: dict[str, list[RepoDataRecord]] = defaultdict(list)
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


def _format_run_exports_lines(run_exports: RunExportsJson | None) -> list[str]:
    if run_exports is None:
        return []
    lines: list[str] = []
    sections = (
        ("weak", run_exports.weak),
        ("strong", run_exports.strong),
        ("noarch", run_exports.noarch),
        ("weak_constrains", run_exports.weak_constrains),
        ("strong_constrains", run_exports.strong_constrains),
    )
    for label, values in sections:
        lines.extend(f"{label}: {escape(value)}" for value in values)
    return lines


def build_version_details_data(
    package_name: str,
    record: RepoDataRecord,
    *,
    package_paths: Sequence[str] | None = None,
    package_paths_error: str | None = None,
    repository_urls: Sequence[str] | None = None,
    documentation_urls: Sequence[str] | None = None,
    homepage_urls: Sequence[str] | None = None,
    recipe_maintainers: Sequence[str] | None = None,
    provenance_remote_url: str | None = None,
    provenance_sha: str | None = None,
    rattler_build_version: str | None = None,
    run_exports: RunExportsJson | None = None,
) -> VersionDetailsData:
    name_value = record.name.source
    metadata_rows: list[tuple[str, str]] = [
        ("Package", escape(package_name)),
        ("Name", escape(name_value)),
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
        ("Package URL", format_clickable_url(str(record.url))),
    ]
    if repository_urls:
        metadata_rows.append(
            (
                "Repository",
                ", ".join(format_clickable_url(url) for url in repository_urls),
            )
        )
    if documentation_urls:
        metadata_rows.append(
            (
                "Documentation",
                ", ".join(format_clickable_url(url) for url in documentation_urls),
            )
        )
    if homepage_urls:
        metadata_rows.append(
            (
                "Homepage",
                ", ".join(format_clickable_url(url) for url in homepage_urls),
            )
        )
    if recipe_maintainers:
        metadata_rows.append(
            (
                "Recipe maintainers",
                ", ".join(
                    format_clickable_github_handle(handle)
                    for handle in recipe_maintainers
                ),
            )
        )
    provenance_lines = format_provenance(provenance_remote_url, provenance_sha)
    if provenance_lines:
        metadata_rows.append(("Provenance", provenance_lines[0].split(": ", 1)[1]))

    if rattler_build_version:
        metadata_rows.append(
            (
                "Built with",
                f"rattler-build {escape(rattler_build_version)}",
            )
        )
    metadata_lines = format_detail_rows(metadata_rows)

    if package_paths_error is not None:
        file_lines = [f"Unavailable: {escape(package_paths_error)}"]
    elif package_paths:
        file_lines = [escape(path) for path in package_paths]
    else:
        file_lines = ["No files listed."]

    dependencies = (
        tuple(escape(str(dependency)) for dependency in record.depends)
        if record.depends
        else ("No dependencies.",)
    )
    constraints = (
        tuple(escape(str(constraint)) for constraint in record.constrains)
        if record.constrains
        else ("No constraints.",)
    )
    run_export_lines = tuple(_format_run_exports_lines(run_exports)) or (
        "No run exports.",
    )

    return VersionDetailsData(
        metadata_lines=tuple(metadata_lines),
        dependencies=dependencies,
        constraints=constraints,
        run_exports=run_export_lines,
        files=tuple(file_lines),
    )


def render_selected_version_details(
    package_name: str,
    record: RepoDataRecord,
    *,
    content_width: int,
    package_paths: Sequence[str] | None = None,
    package_paths_error: str | None = None,
    repository_urls: Sequence[str] | None = None,
    documentation_urls: Sequence[str] | None = None,
    homepage_urls: Sequence[str] | None = None,
    recipe_maintainers: Sequence[str] | None = None,
    provenance_remote_url: str | None = None,
    provenance_sha: str | None = None,
    rattler_build_version: str | None = None,
) -> str:
    name_value = record.name.source
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
    recipe_maintainer_lines = format_clickable_github_handle_list(
        "Recipe maintainers:", recipe_maintainers
    )
    provenance_lines = format_provenance(provenance_remote_url, provenance_sha)
    built_using_lines = (
        [f"Built with rattler-build {escape(rattler_build_version)}"]
        if rattler_build_version
        else []
    )
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
            *([""] + recipe_maintainer_lines if recipe_maintainer_lines else []),
            *([""] + provenance_lines if provenance_lines else []),
            *([""] + built_using_lines if built_using_lines else []),
            "",
            *dependencies,
            "",
            *constrains,
            "",
            *file_lines,
        ]
    )
    return "\n".join(lines)
