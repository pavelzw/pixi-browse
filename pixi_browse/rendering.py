from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlparse

from rattler.exceptions import InvalidMatchSpecError
from rattler.match_spec import MatchSpec
from rattler.package import RunExportsJson
from rattler.repo_data import RepoDataRecord
from rattler.version import VersionWithSource
from rich.markup import escape

from pixi_browse.models import (
    CompareFileRow,
    CompareRow,
    CompareSelection,
    MetadataRow,
    PackageFile,
    VersionArtifactData,
    VersionCompareData,
    VersionDetailsData,
)


def _provenance_link(remote_url: str | None, sha: str | None) -> tuple[str, str] | None:
    if not remote_url or not sha:
        return None

    parsed = urlparse(remote_url)
    path_parts = [part for part in parsed.path.removesuffix(".git").split("/") if part]
    if parsed.netloc == "github.com" and len(path_parts) >= 2:
        slug = "/".join(path_parts[:2])
        return f"{slug}@{sha}", f"https://github.com/{slug}/commit/{sha}"
    return f"{remote_url}@{sha}", remote_url


def format_detail_rows(rows: Sequence[tuple[str, str]]) -> list[str]:
    if not rows:
        return []
    label_width = max(len(label) for label, _ in rows)
    return [f"{label:<{label_width}}  {value}" for label, value in rows]


def format_clickable_url(url: str) -> str:
    return format_clickable_link(escape(url), url)


def format_clickable_link(label: str, url: str) -> str:
    return f"[@click=app.open_external_url({url!r})]{label}[/]"


def format_clickable_github_handle(handle: str) -> str:
    normalized = handle.lstrip("@")
    return format_clickable_link(
        f"@{escape(normalized)}",
        f"https://github.com/{normalized}",
    )


def _clickable_url_list_value(urls: Sequence[str]) -> str:
    return ", ".join(format_clickable_url(url) for url in urls)


def _clickable_recipe_maintainers_value(handles: Sequence[str]) -> str:
    return ", ".join(format_clickable_github_handle(handle) for handle in handles)


def _clickable_provenance_value(remote_url: str | None, sha: str | None) -> str | None:
    provenance_link = _provenance_link(remote_url, sha)
    if provenance_link is None:
        return None
    label, commit_url = provenance_link
    return format_clickable_link(escape(label), commit_url)


def _format_url_value(url: str, *, clickable: bool) -> str:
    if clickable:
        return format_clickable_url(url)
    return escape(url)


def _format_url_list_value(urls: Sequence[str], *, clickable: bool) -> str:
    if clickable:
        return _clickable_url_list_value(urls)
    return ", ".join(escape(url) for url in urls)


def _format_recipe_maintainers_value(handles: Sequence[str], *, clickable: bool) -> str:
    if clickable:
        return _clickable_recipe_maintainers_value(handles)
    return ", ".join(escape(handle) for handle in handles)


def _format_provenance_value(
    remote_url: str | None, sha: str | None, *, clickable: bool
) -> str | None:
    if clickable:
        return _clickable_provenance_value(remote_url, sha)

    provenance_link = _provenance_link(remote_url, sha)
    if provenance_link is None:
        return None
    label, _commit_url = provenance_link
    return escape(label)


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


def format_human_byte_size(value: Any) -> str:
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
    return f"{size:.1f} {unit}"


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


def _format_plain_run_exports_lines(run_exports: RunExportsJson | None) -> list[str]:
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
        lines.extend(f"{label}: {value}" for value in values)
    return lines


def _metadata_rows_for_record(
    package_name: str,
    record: RepoDataRecord,
    *,
    clickable: bool = False,
    repository_urls: Sequence[str] | None = None,
    documentation_urls: Sequence[str] | None = None,
    homepage_urls: Sequence[str] | None = None,
    recipe_maintainers: Sequence[str] | None = None,
    provenance_remote_url: str | None = None,
    provenance_sha: str | None = None,
    rattler_build_version: str | None = None,
) -> tuple[MetadataRow, ...]:
    metadata_rows: list[MetadataRow] = [
        ("Package", package_name),
        ("Name", record.name.source),
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
        ("Package URL", _format_url_value(str(record.url), clickable=clickable)),
    ]
    if repository_urls:
        metadata_rows.append(
            (
                "Repository",
                _format_url_list_value(repository_urls, clickable=clickable),
            )
        )
    if documentation_urls:
        metadata_rows.append(
            (
                "Documentation",
                _format_url_list_value(documentation_urls, clickable=clickable),
            )
        )
    if homepage_urls:
        metadata_rows.append(
            ("Homepage", _format_url_list_value(homepage_urls, clickable=clickable))
        )
    if recipe_maintainers:
        metadata_rows.append(
            (
                "Recipe maintainers",
                _format_recipe_maintainers_value(
                    recipe_maintainers, clickable=clickable
                ),
            )
        )
    provenance_value = _format_provenance_value(
        provenance_remote_url,
        provenance_sha,
        clickable=clickable,
    )
    if provenance_value is not None:
        metadata_rows.append(("Provenance", provenance_value))
    if rattler_build_version:
        metadata_rows.append(
            ("Built with", f"rattler-build {escape(rattler_build_version)}")
        )
    return tuple(metadata_rows)


def build_version_artifact_data(
    package_name: str,
    record: RepoDataRecord,
    *,
    package_paths: Sequence[PackageFile] | None = None,
    repository_urls: Sequence[str] | None = None,
    documentation_urls: Sequence[str] | None = None,
    homepage_urls: Sequence[str] | None = None,
    recipe_maintainers: Sequence[str] | None = None,
    provenance_remote_url: str | None = None,
    provenance_sha: str | None = None,
    rattler_build_version: str | None = None,
    run_exports: RunExportsJson | None = None,
) -> VersionArtifactData:
    return VersionArtifactData(
        metadata_rows=_metadata_rows_for_record(
            package_name,
            record,
            repository_urls=repository_urls,
            documentation_urls=documentation_urls,
            homepage_urls=homepage_urls,
            recipe_maintainers=recipe_maintainers,
            provenance_remote_url=provenance_remote_url,
            provenance_sha=provenance_sha,
            rattler_build_version=rattler_build_version,
        ),
        dependencies=tuple(str(dependency) for dependency in record.depends or ()),
        constraints=tuple(str(constraint) for constraint in record.constrains or ()),
        file_paths=tuple(package_paths or ()),
        run_exports=run_exports,
    )


def _matchspec_key_for_line(line: str, *, run_export: bool) -> str | None:
    candidate = line.split(": ", 1)[1] if run_export and ": " in line else line
    try:
        return MatchSpec(candidate, exact_names_only=False).name.normalized
    except InvalidMatchSpecError:
        return None


def _diff_dependency_group(
    left: Sequence[str], right: Sequence[str], *, run_export: bool
) -> tuple[CompareRow, ...]:
    left_grouped: dict[str, list[str]] = {}
    right_grouped: dict[str, list[str]] = {}
    ordered_keys: list[str] = []
    unmatched_left: list[str] = []
    unmatched_right: list[str] = []

    for line in left:
        key = _matchspec_key_for_line(line, run_export=run_export)
        if key is None:
            unmatched_left.append(line)
            continue
        left_grouped.setdefault(key, []).append(line)
        if key not in ordered_keys:
            ordered_keys.append(key)

    for line in right:
        key = _matchspec_key_for_line(line, run_export=run_export)
        if key is None:
            unmatched_right.append(line)
            continue
        right_grouped.setdefault(key, []).append(line)
        if key not in ordered_keys:
            ordered_keys.append(key)

    rows: list[CompareRow] = []
    for key in ordered_keys:
        left_lines = list(left_grouped.get(key, ()))
        right_lines = list(right_grouped.get(key, ()))

        while left_lines and right_lines:
            left_line = left_lines.pop(0)
            right_line = right_lines.pop(0)
            rows.append(
                CompareRow(
                    label=key,
                    left=left_line,
                    right=right_line,
                    changed=left_line != right_line,
                )
            )

        rows.extend(
            CompareRow(label=key, left=line, right="", changed=True)
            for line in left_lines
        )
        rows.extend(
            CompareRow(label=key, left="", right=line, changed=True)
            for line in right_lines
        )

    remaining_right = list(unmatched_right)
    for line in unmatched_left:
        if line in remaining_right:
            remaining_right.remove(line)
            rows.append(CompareRow(label=line, left=line, right=line, changed=False))
        else:
            rows.append(
                CompareRow(
                    label=line,
                    left=line,
                    right="",
                    changed=True,
                )
            )
    rows.extend(
        CompareRow(label=line, left="", right=line, changed=True)
        for line in remaining_right
    )
    return tuple(rows)


def _package_file_summary(package_file: PackageFile) -> str:
    details: list[str] = []
    if package_file.size_in_bytes is not None:
        details.append(format_human_byte_size(package_file.size_in_bytes))
    if package_file.path_type is not None:
        details.append(package_file.path_type)
    if package_file.no_link is not None:
        details.append(f"no_link={package_file.no_link}")
    if package_file.sha256 is not None:
        details.append(f"sha256={package_file.sha256.hex()[:8]}")
    if not details:
        return package_file.path
    return f"{package_file.path} ({', '.join(details)})"


def _files_differ(left: PackageFile, right: PackageFile) -> bool:
    if left.sha256 is not None or right.sha256 is not None:
        if left.sha256 != right.sha256:
            return True
    return (
        left.size_in_bytes != right.size_in_bytes
        or left.no_link != right.no_link
        or left.path_type != right.path_type
    )


def _diff_file_paths(
    left_files: Sequence[PackageFile], right_files: Sequence[PackageFile]
) -> tuple[CompareFileRow, ...]:
    left_by_path = {package_file.path: package_file for package_file in left_files}
    right_by_path = {package_file.path: package_file for package_file in right_files}
    ordered_paths = list(left_by_path)
    ordered_paths.extend(path for path in right_by_path if path not in left_by_path)

    rows: list[CompareFileRow] = []
    for path in ordered_paths:
        left_file = left_by_path.get(path)
        right_file = right_by_path.get(path)
        if left_file is None and right_file is not None:
            rows.append(
                CompareFileRow(
                    label=path,
                    left="",
                    right=_package_file_summary(right_file),
                    changed=True,
                    right_file=right_file,
                )
            )
            continue
        if right_file is None and left_file is not None:
            rows.append(
                CompareFileRow(
                    label=path,
                    left=_package_file_summary(left_file),
                    right="",
                    changed=True,
                    left_file=left_file,
                )
            )
            continue
        assert left_file is not None and right_file is not None
        rows.append(
            CompareFileRow(
                label=path,
                left=_package_file_summary(left_file),
                right=_package_file_summary(right_file),
                changed=_files_differ(left_file, right_file),
                left_file=left_file,
                right_file=right_file,
            )
        )
    return tuple(rows)


def _build_file_compare_rows(
    left_artifact: VersionArtifactData, right_artifact: VersionArtifactData
) -> tuple[CompareFileRow, ...]:
    return _diff_file_paths(left_artifact.file_paths, right_artifact.file_paths)


def build_version_compare_data(
    left_selection: CompareSelection,
    left_artifact: VersionArtifactData,
    right_selection: CompareSelection,
    right_artifact: VersionArtifactData,
) -> VersionCompareData:
    left_run_exports = tuple(_format_plain_run_exports_lines(left_artifact.run_exports))
    right_run_exports = tuple(
        _format_plain_run_exports_lines(right_artifact.run_exports)
    )

    left_metadata = dict(left_artifact.metadata_rows)
    right_metadata = dict(right_artifact.metadata_rows)
    ordered_labels = [label for label, _value in left_artifact.metadata_rows]
    ordered_labels.extend(
        label
        for label, _value in right_artifact.metadata_rows
        if label not in left_metadata
    )
    metadata_rows = tuple(
        CompareRow(
            label=label,
            left=left_metadata.get(label, "not available"),
            right=right_metadata.get(label, "not available"),
            changed=(
                left_metadata.get(label, "not available")
                != right_metadata.get(label, "not available")
            ),
        )
        for label in ordered_labels
    )

    return VersionCompareData(
        left_selection=left_selection,
        right_selection=right_selection,
        metadata_rows=metadata_rows,
        dependencies=_diff_dependency_group(
            left_artifact.dependencies,
            right_artifact.dependencies,
            run_export=False,
        ),
        constraints=_diff_dependency_group(
            left_artifact.constraints,
            right_artifact.constraints,
            run_export=False,
        ),
        run_exports=_diff_dependency_group(
            left_run_exports,
            right_run_exports,
            run_export=True,
        ),
        files=_build_file_compare_rows(left_artifact, right_artifact),
    )


def build_version_details_data(
    package_name: str,
    record: RepoDataRecord,
    *,
    package_paths: Sequence[PackageFile] | None = None,
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
    metadata_rows = _metadata_rows_for_record(
        package_name,
        record,
        clickable=True,
        repository_urls=repository_urls,
        documentation_urls=documentation_urls,
        homepage_urls=homepage_urls,
        recipe_maintainers=recipe_maintainers,
        provenance_remote_url=provenance_remote_url,
        provenance_sha=provenance_sha,
        rattler_build_version=rattler_build_version,
    )
    metadata_lines = format_detail_rows(metadata_rows)

    if package_paths_error is not None:
        file_lines = [f"Unavailable: {escape(package_paths_error)}"]
    elif package_paths:
        file_lines = [escape(package_file.path) for package_file in package_paths]
    else:
        file_lines = ["No files listed."]

    dependencies = (
        tuple(escape(str(dependency)) for dependency in record.depends)
        if record.depends
        else ()
    )
    constraints = (
        tuple(escape(str(constraint)) for constraint in record.constrains)
        if record.constrains
        else ()
    )
    run_export_lines = tuple(_format_run_exports_lines(run_exports))

    return VersionDetailsData(
        metadata_lines=tuple(metadata_lines),
        dependencies=dependencies,
        constraints=constraints,
        run_exports=run_export_lines,
        files=tuple(file_lines),
        file_paths=tuple(package_paths or ()),
    )
