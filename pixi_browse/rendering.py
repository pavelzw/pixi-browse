from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import urlparse

from rattler.channel import Channel
from rattler.exceptions import InvalidMatchSpecError
from rattler.match_spec import MatchSpec
from rattler.package import IndexJson, NoArchType, RunExportsJson
from rattler.repo_data import ChannelNotice, RepoDataRecord
from rattler.sigstore import CertificateClaims, VerifiedAttestation, VerifiedChecks
from rich.markup import escape
from rich.text import Text

from pixi_browse.models import (
    AttestationData,
    AttestationStatus,
    CompareFileRow,
    CompareRow,
    CompareSelection,
    MetadataRow,
    PackageFile,
    RepodataPatchDiff,
    VersionArtifactData,
    VersionCompareData,
)
from pixi_browse.platform_utils import sort_subdirs_by_latest_version

_SYNTAX_LEXERS_BY_SUFFIX: dict[str, str] = {
    ".bash": "bash",
    ".bat": "batch",
    ".c": "c",
    ".cc": "cpp",
    ".cfg": "ini",
    ".cmd": "batch",
    ".conf": "ini",
    ".cpp": "cpp",
    ".css": "css",
    ".cts": "typescript",
    ".cxx": "cpp",
    ".fish": "fish",
    ".go": "go",
    ".h": "c",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".htm": "html",
    ".html": "html",
    ".hxx": "cpp",
    ".ini": "ini",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "jsx",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".markdown": "markdown",
    ".md": "markdown",
    ".mjs": "javascript",
    ".mts": "typescript",
    ".pl": "perl",
    ".pm": "perl",
    ".ps1": "powershell",
    ".py": "python",
    ".pyi": "python",
    ".r": "r",
    ".rb": "ruby",
    ".rs": "rust",
    ".rst": "rst",
    ".scss": "scss",
    ".sh": "bash",
    ".sql": "sql",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".zsh": "zsh",
}

_SYNTAX_LEXERS_BY_FILENAME: dict[str, str] = {
    "cmakelists.txt": "cmake",
    "dockerfile": "docker",
    "makefile": "make",
}


def syntax_lexer_for_path(file_path: str) -> str | None:
    """Return a Rich/Pygments lexer alias for a supported package file."""
    path = PurePosixPath(file_path)
    filename = path.name.lower()
    lexer = _SYNTAX_LEXERS_BY_FILENAME.get(filename)
    if lexer is not None:
        return lexer
    return _SYNTAX_LEXERS_BY_SUFFIX.get(path.suffix.lower())


def _github_slug(remote_url: str | None) -> str | None:
    """The ``owner/repo`` of a GitHub URL, or ``None`` for anything else.

    Only GitHub gets one: it is the host whose URL layout the app knows well
    enough to build a commit or repository link from a bare slug.
    """
    if not remote_url:
        return None
    parsed = urlparse(remote_url)
    if parsed.netloc != "github.com":
        return None
    path_parts = [part for part in parsed.path.removesuffix(".git").split("/") if part]
    if len(path_parts) < 2:
        return None
    return "/".join(path_parts[:2])


def _provenance_link(remote_url: str | None, sha: str | None) -> tuple[str, str] | None:
    if not remote_url or not sha:
        return None

    slug = _github_slug(remote_url)
    if slug is not None:
        return f"{slug}@{sha}", f"https://github.com/{slug}/commit/{sha}"
    return f"{remote_url}@{sha}", remote_url


def format_clickable_url(url: str) -> str:
    return format_clickable_link(escape(url), url)


def format_clickable_link(label: str, url: str) -> str:
    """Mark ``label`` up as an OSC-8 hyperlink to ``url``.

    The terminal owns the link, not the app: it opens with the modifier-click
    the user already knows from every other link in their terminal, and it
    lands in the browser of whoever is looking at the screen rather than in one
    on the host a remote session runs on.
    """
    # Textual reads a style value up to the next quote, so a literal one in the
    # URL has to travel percent-encoded.
    quoted_url = url.replace("'", "%27")
    return f"[underline link='{quoted_url}']{label}[/]"


def format_clickable_github_handle(handle: str) -> str:
    normalized = handle.lstrip("@")
    return format_clickable_link(
        f"@{escape(normalized)}", _github_handle_url(normalized)
    )


def _github_handle_url(handle: str) -> str:
    # ``org/team`` handles name a GitHub team, whose page lives under the
    # organization rather than at ``github.com/org/team``.
    organization, separator, team = handle.partition("/")
    if separator and organization and team:
        return f"https://github.com/orgs/{organization}/teams/{team}"
    return f"https://github.com/{handle}"


def _clickable_url_list_value(urls: Sequence[str]) -> str:
    return ", ".join(format_clickable_url(url) for url in urls)


def _clickable_recipe_maintainers_value(handles: Sequence[str]) -> str:
    return ", ".join(format_clickable_github_handle(handle) for handle in handles)


def _url_list_value(urls: Sequence[str]) -> str:
    return ", ".join(escape(url) for url in urls)


def _clickable_provenance_value(remote_url: str | None, sha: str | None) -> str | None:
    provenance_link = _provenance_link(remote_url, sha)
    if provenance_link is None:
        return None
    label, commit_url = provenance_link
    return format_clickable_link(escape(label), commit_url)


def _provenance_value(remote_url: str | None, sha: str | None) -> str | None:
    provenance_link = _provenance_link(remote_url, sha)
    if provenance_link is None:
        return None
    label, _commit_url = provenance_link
    return escape(label)


def format_record_value(value: object) -> str:
    if value is None:
        return "not available"
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, list):
        if not value:
            return "none"
        return ", ".join(str(item) for item in value)
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value)
    if text == "NoArchType(None)":
        return "none"
    return escape(text)


def _scale_byte_size(value: int) -> tuple[float, str]:
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    size = float(value)
    unit = units[0]
    for candidate in units:
        unit = candidate
        if size < 1024.0 or candidate == units[-1]:
            break
        size /= 1024.0
    return size, unit


def format_byte_size(value: int | None) -> str:
    """Render a repodata size with the exact byte count, e.g. ``1.5 KiB (1,536 bytes)``."""
    if value is None:
        return "not available"
    size, unit = _scale_byte_size(value)
    if unit == "B":
        return f"{value:,} B"
    return f"{size:.1f} {unit} ({value:,} bytes)"


def format_human_byte_size(value: int) -> str:
    """Render a file size compactly, e.g. ``1.5 KiB``."""
    size, unit = _scale_byte_size(value)
    if unit == "B":
        return f"{value:,} B"
    return f"{size:.1f} {unit}"


# Marker and colour of a channel notice, by CEP-6 level.
CHANNEL_NOTICE_STYLES: dict[str, tuple[str, str]] = {
    "critical": ("!!", "bold red"),
    "warning": ("!", "yellow"),
    "info": ("i", "cyan"),
}


def format_channel_notice_date(timestamp: str | None) -> str | None:
    """The calendar date of a CEP-6 timestamp, or ``None`` if it has none."""
    if timestamp is None:
        return None
    try:
        return datetime.fromisoformat(timestamp).date().isoformat()
    except ValueError:
        return timestamp


def channel_notice_channel_label(notice: ChannelNotice) -> str:
    """The channel a notice belongs to, as a short name where rattler can
    derive one from the channel URL (``bioconda`` for
    ``https://conda.anaconda.org/bioconda/``), otherwise the URL itself."""
    return Channel(notice.channel).name or notice.channel


def render_channel_notice_heading(notice: ChannelNotice) -> Text:
    """Render the heading line of a channel notice for the channel dialog.

    It names the level, the channel and the publication date in the level's
    colour. The message is shown separately, indented under the heading, so a
    long or multi-line message wraps without hiding where it came from.
    """
    marker, style = CHANNEL_NOTICE_STYLES[notice.level]
    text = Text()
    text.append(f"{marker:>2} ", style=style)
    text.append(notice.level.upper(), style=style)
    text.append(" · ", style="dim")
    text.append(channel_notice_channel_label(notice), style=style)
    created = format_channel_notice_date(notice.created_at)
    if created is not None:
        text.append(f" · {created}", style="dim")
    return text


def render_channel_notice_message(notice: ChannelNotice) -> Text:
    """The message of a channel notice, as plain text (never Rich markup)."""
    return Text(notice.message.strip())


def render_package_preview(
    package_name: str,
    records: list[RepoDataRecord],
) -> str:
    """Render the package preview. Expects `records` sorted newest first."""
    if not records:
        return f"# {package_name}\n\nNo metadata records found."

    grouped_by_subdir: dict[str, list[RepoDataRecord]] = defaultdict(list)
    for record in records:
        grouped_by_subdir[record.subdir].append(record)

    sorted_subdirs = sort_subdirs_by_latest_version(
        grouped_by_subdir,
        lambda record: record.version,
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
    repository_urls: Sequence[str] = (),
    documentation_urls: Sequence[str] = (),
    homepage_urls: Sequence[str] = (),
    recipe_maintainers: Sequence[str] = (),
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
        ("Indexed Timestamp", format_record_value(record.indexed_timestamp)),
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
        ("Package URL", escape(str(record.url))),
    ]
    if repository_urls:
        metadata_rows.append(("Repository", _url_list_value(repository_urls)))
    if documentation_urls:
        metadata_rows.append(("Documentation", _url_list_value(documentation_urls)))
    if homepage_urls:
        metadata_rows.append(("Homepage", _url_list_value(homepage_urls)))
    if recipe_maintainers:
        metadata_rows.append(
            (
                "Recipe maintainers",
                ", ".join(escape(handle) for handle in recipe_maintainers),
            )
        )
    provenance_value = _provenance_value(provenance_remote_url, provenance_sha)
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
    package_paths: Sequence[PackageFile] = (),
    info_files: Sequence[PackageFile] = (),
    repository_urls: Sequence[str] = (),
    documentation_urls: Sequence[str] = (),
    homepage_urls: Sequence[str] = (),
    recipe_maintainers: Sequence[str] = (),
    provenance_remote_url: str | None = None,
    provenance_sha: str | None = None,
    rattler_build_version: str | None = None,
    run_exports: RunExportsJson | None = None,
    repodata_patches: RepodataPatchDiff = RepodataPatchDiff(),
    attestation: AttestationData = AttestationData(),
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
        extra_depends=tuple(
            (group, tuple(dependencies))
            for group, dependencies in sorted(record.extra_depends.items())
        ),
        package_url=str(record.url),
        file_paths=tuple(package_paths),
        info_files=tuple(info_files),
        run_exports=run_exports,
        repository_urls=tuple(repository_urls),
        documentation_urls=tuple(documentation_urls),
        homepage_urls=tuple(homepage_urls),
        recipe_maintainers=tuple(recipe_maintainers),
        provenance_remote_url=provenance_remote_url,
        provenance_sha=provenance_sha,
        rattler_build_version=rattler_build_version,
        repodata_patches=repodata_patches,
        attestation=attestation,
    )


def _repodata_field_text(value: object) -> str:
    """Render an ``index.json``/repodata field for a plain-text diff cell."""
    if value is None:
        return ""
    if isinstance(value, NoArchType):
        if value.python:
            return "python"
        if value.generic:
            return "generic"
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_repodata_patch_diff(
    record: RepoDataRecord, index_json: IndexJson
) -> RepodataPatchDiff:
    """Diff the archive's ``info/index.json`` against the channel's repodata record.

    ``record`` comes from the gateway and therefore already has the channel's
    repodata patches applied. ``index_json`` is read from the package archive
    and is what the package was built with. Every field that differs is a
    repodata patch. Field labels follow the ``index.json`` key names.

    ``arch`` and ``platform`` are deliberately not compared: the indexer drops
    them from repodata because they are implied by ``subdir``, so they differ
    for every package without any patch being involved.

    ``license_family`` is not compared either: conda-forge's repodata patches
    fill it in for every record that lacks it, which is every rattler-build
    package, so it would flag nearly all recent artifacts as patched. See
    https://github.com/conda-forge/conda-forge-repodata-patches-feedstock/blob/98e5f9bcb6a31f56d168a7e343c7ad70c784e194/recipe/gen_patch_json.py#L600-L603

    ``purls`` and ``repodata_revision`` are available on ``IndexJson`` but not on
    py-rattler's ``PackageRecord``, so they cannot be compared. ``indexed_timestamp``
    is the other way around: the channel index assigns it (CEP-0047), so it exists
    only on the record and is not a patch of anything the package was built with.
    """
    scalar_fields: tuple[tuple[str, object, object], ...] = (
        ("version", index_json.version, record.version),
        ("build", index_json.build, record.build),
        ("build_number", index_json.build_number, record.build_number),
        ("license", index_json.license, record.license),
        ("features", index_json.features, record.features),
        ("track_features", index_json.track_features, record.track_features),
        ("timestamp", index_json.timestamp, record.timestamp),
        ("noarch", index_json.noarch, record.noarch),
        (
            "python_site_packages_path",
            index_json.python_site_packages_path,
            record.python_site_packages_path,
        ),
        ("flags", index_json.flags, record.flags),
    )
    metadata_rows: list[CompareRow] = []
    for label, unpatched, patched in scalar_fields:
        left = _repodata_field_text(unpatched)
        right = _repodata_field_text(patched)
        if left != right:
            metadata_rows.append(
                CompareRow(label=label, left=left, right=right, changed=True)
            )
    # The indexer always fills in `subdir`; older packages lack it in index.json.
    if index_json.subdir is not None and index_json.subdir != record.subdir:
        metadata_rows.append(
            CompareRow(
                label="subdir",
                left=index_json.subdir,
                right=record.subdir,
                changed=True,
            )
        )

    dependency_rows: list[CompareRow] = [
        CompareRow(label="depends", left=row.left, right=row.right, changed=True)
        for row in _diff_dependency_group(
            index_json.depends, record.depends, run_export=False
        )
        if row.changed
    ]
    # Extra dependency groups (`pkg[extras=["group"]]`) are patched per group.
    unpatched_extra_depends = index_json.extra_depends
    patched_extra_depends = record.extra_depends
    for group in sorted({*unpatched_extra_depends, *patched_extra_depends}):
        dependency_rows.extend(
            CompareRow(
                label=f"extra_depends[{group}]",
                left=row.left,
                right=row.right,
                changed=True,
            )
            for row in _diff_dependency_group(
                unpatched_extra_depends.get(group, []),
                patched_extra_depends.get(group, []),
                run_export=False,
            )
            if row.changed
        )
    dependencies = tuple(dependency_rows)
    constraints = tuple(
        CompareRow(label="constrains", left=row.left, right=row.right, changed=True)
        for row in _diff_dependency_group(
            index_json.constrains, record.constrains, run_export=False
        )
        if row.changed
    )
    return RepodataPatchDiff(
        metadata=tuple(metadata_rows),
        dependencies=dependencies,
        constraints=constraints,
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
    if package_file.link_target is not None:
        details.append(f"target={package_file.link_target}")
    if package_file.no_link is not None:
        details.append(f"no_link={package_file.no_link}")
    if package_file.sha256 is not None:
        details.append(f"sha256={package_file.sha256.hex()[:8]}")
    if not details:
        return package_file.path
    return f"{package_file.path} ({', '.join(details)})"


def _files_differ(left: PackageFile, right: PackageFile) -> bool:
    if (
        left.sha256 is not None
        and right.sha256 is not None
        and left.sha256 != right.sha256
    ):
        return True
    if left.is_symlink != right.is_symlink:
        return True
    if (
        left.is_symlink
        and right.is_symlink
        and left.link_target is not None
        and right.link_target is not None
        and left.link_target != right.link_target
    ):
        return True
    return (
        (
            left.size_in_bytes is not None
            and right.size_in_bytes is not None
            and left.size_in_bytes != right.size_in_bytes
        )
        or (
            left.no_link is not None
            and right.no_link is not None
            and left.no_link != right.no_link
        )
        or (
            left.path_type is not None
            and right.path_type is not None
            and left.path_type != right.path_type
        )
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


def _display_info_compare_rows(
    rows: Sequence[CompareFileRow], *, common_comparison_known: bool
) -> tuple[CompareFileRow, ...]:
    return tuple(
        CompareFileRow(
            label=row.label.removeprefix("info/"),
            left=row.left,
            right=row.right,
            changed=row.changed,
            left_file=row.left_file,
            right_file=row.right_file,
            comparison_known=(
                common_comparison_known
                or row.changed
                or (
                    row.left_file is not None
                    and row.right_file is not None
                    and row.left_file.is_symlink
                    and row.right_file.is_symlink
                    and row.left_file.link_target is not None
                    and row.right_file.link_target is not None
                )
                if row.left_file is not None and row.right_file is not None
                else True
            ),
        )
        for row in rows
    )


def _build_info_file_compare_rows(
    left_artifact: VersionArtifactData, right_artifact: VersionArtifactData
) -> tuple[CompareFileRow, ...]:
    rows = _diff_file_paths(left_artifact.info_files, right_artifact.info_files)
    return _display_info_compare_rows(rows, common_comparison_known=False)


def resolve_info_file_compare_rows(
    rows: Sequence[CompareFileRow],
    *,
    left_sha256: dict[str, bytes],
    right_sha256: dict[str, bytes],
) -> tuple[CompareFileRow, ...]:
    left_files = tuple(
        PackageFile(
            path=row.left_file.path,
            size_in_bytes=row.left_file.size_in_bytes,
            sha256=left_sha256.get(row.left_file.path),
            no_link=row.left_file.no_link,
            path_type=row.left_file.path_type,
            link_target=row.left_file.link_target,
        )
        for row in rows
        if row.left_file is not None
    )
    right_files = tuple(
        PackageFile(
            path=row.right_file.path,
            size_in_bytes=row.right_file.size_in_bytes,
            sha256=right_sha256.get(row.right_file.path),
            no_link=row.right_file.no_link,
            path_type=row.right_file.path_type,
            link_target=row.right_file.link_target,
        )
        for row in rows
        if row.right_file is not None
    )
    return _display_info_compare_rows(
        _diff_file_paths(left_files, right_files),
        common_comparison_known=True,
    )


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

    left_extras = dict(left_artifact.extra_depends)
    right_extras = dict(right_artifact.extra_depends)

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
        extra_depends=tuple(
            CompareRow(
                label=group,
                left=row.left,
                right=row.right,
                changed=row.changed,
            )
            for group in sorted(left_extras.keys() | right_extras.keys())
            for row in _diff_dependency_group(
                left_extras.get(group, ()),
                right_extras.get(group, ()),
                run_export=False,
            )
        ),
        files=_build_file_compare_rows(left_artifact, right_artifact),
        info_files=_build_info_file_compare_rows(left_artifact, right_artifact),
    )


def build_version_details_metadata_rows(
    artifact: VersionArtifactData,
) -> tuple[MetadataRow, ...]:
    """The metadata tab: the artifact's own rows, with every URL in them made
    clickable."""
    clickable_rows: list[MetadataRow] = []
    for label, value in artifact.metadata_rows:
        if label == "Package URL":
            clickable_rows.append((label, format_clickable_url(artifact.package_url)))
            continue
        if label == "Repository":
            clickable_rows.append(
                (label, _clickable_url_list_value(artifact.repository_urls))
            )
            continue
        if label == "Documentation":
            clickable_rows.append(
                (label, _clickable_url_list_value(artifact.documentation_urls))
            )
            continue
        if label == "Homepage":
            clickable_rows.append(
                (label, _clickable_url_list_value(artifact.homepage_urls))
            )
            continue
        if label == "Recipe maintainers":
            clickable_rows.append(
                (
                    label,
                    _clickable_recipe_maintainers_value(artifact.recipe_maintainers),
                )
            )
            continue
        if label == "Provenance":
            clickable_provenance = _clickable_provenance_value(
                artifact.provenance_remote_url,
                artifact.provenance_sha,
            )
            clickable_rows.append((label, clickable_provenance or value))
            continue
        clickable_rows.append((label, value))
    return tuple(clickable_rows)


ATTESTATION_VERDICTS: dict[AttestationStatus, str] = {
    "unsigned": "Unsigned",
    "verified": "[bold green]✓ Verified[/]",
    "unverified": "[bold red]✗ Not verified[/]",
}


def describe_attestation_checks(checks: VerifiedChecks) -> str:
    """Name the parts of the verification that were performed.

    A signature can verify without everything around it having been checked, so
    the verdict says what was established instead of leaving "verified" to be
    read as all of it: a bundle carrying only an inclusion *promise* is signed
    by the log but not proven to be in it, and one without a transparency log
    entry at all is a bare signature.
    """
    performed = []
    if checks.certificate_chain:
        performed.append("certificate chain")
    if checks.signed_certificate_timestamp:
        performed.append("SCT")
    if checks.transparency_log:
        performed.append(
            "log inclusion proof" if checks.inclusion_proof else "log inclusion promise"
        )
    return ", ".join(performed) or "signature only"


def format_attestation_verdict(attestation: AttestationData) -> str:
    """The headline of the attestation tab: the outcome and what it rests on."""
    verified = attestation.attestation
    if verified is not None:
        detail = describe_attestation_checks(verified.checks)
    elif attestation.status == "unverified":
        detail = "see the warnings below"
    else:
        detail = "this channel advertises no attestations for the artifact"
    return f"{ATTESTATION_VERDICTS[attestation.status]} - {detail}"


def _attestation_build_rows(claims: CertificateClaims) -> list[MetadataRow]:
    """The story of the build, as the signing certificate tells it: which commit
    of which repository was built, by what, on what."""
    rows: list[MetadataRow] = []
    repository = claims.source_repository_uri
    if repository is not None:
        # The identifier does not change when a repository is renamed, so it is
        # what a trust policy should be pinned to, and the visibility is the one
        # as of signing rather than the one the repository has now.
        annotations = [
            annotation
            for annotation in (
                claims.source_repository_visibility_at_signing,
                f"id {claims.source_repository_identifier}"
                if claims.source_repository_identifier is not None
                else None,
            )
            if annotation
        ]
        slug = _github_slug(repository)
        value = format_clickable_link(escape(slug or repository), repository)
        if annotations:
            value += f" ({escape(', '.join(annotations))})"
        rows.append(("Repository", value))
    commit = claims.source_repository_digest
    if commit is not None:
        slug = _github_slug(repository)
        value = (
            format_clickable_link(
                escape(commit), f"https://github.com/{slug}/commit/{commit}"
            )
            if slug is not None
            else escape(commit)
        )
        reference = claims.source_repository_ref
        if reference is not None:
            value += f" on {escape(reference)}"
        rows.append(("Commit", value))
    workflow = claims.build_config_uri
    if workflow is not None:
        label = escape(_shorten_workflow_uri(workflow, claims))
        link = _workflow_link(workflow, claims)
        value = label if link is None else format_clickable_link(label, link)
        if claims.build_trigger is not None:
            value += f" (trigger: {escape(claims.build_trigger)})"
        rows.append(("Workflow", value))
    if claims.runner_environment is not None:
        rows.append(("Runner", escape(claims.runner_environment)))
    run = claims.run_invocation_uri
    if run is not None:
        rows.append(("Build", format_clickable_link(escape(_describe_run(run)), run)))
    return rows


def _shorten_workflow_uri(workflow_uri: str, claims: CertificateClaims) -> str:
    """Reduce a build config URI to the path within its own repository.

    The repository and the ref are shown on their own rows, so repeating them
    here only pushes the file name off the screen. A reusable workflow from
    another repository keeps its full URI, which is the point of showing it.
    """
    workflow = workflow_uri
    if claims.source_repository_uri is not None:
        workflow = workflow.removeprefix(f"{claims.source_repository_uri}/")
    if claims.source_repository_ref is not None:
        workflow = workflow.removesuffix(f"@{claims.source_repository_ref}")
    return workflow


def _url_ref(reference: str) -> str:
    """A git ref as GitHub spells it in a URL: ``refs/heads/main`` is ``main``."""
    for prefix in ("refs/heads/", "refs/tags/"):
        if reference.startswith(prefix):
            return reference.removeprefix(prefix)
    return reference


def _workflow_link(workflow_uri: str, claims: CertificateClaims) -> str | None:
    """The page of the workflow file a build config URI names, if it has one.

    The URI is not one: GitHub spells it
    ``https://github.com/<owner>/<repo>/<path>@<ref>``, which resolves to
    nothing when opened. The file behind it does have a page, and the
    certificate says which commit of it was built, so that revision is what the
    row links to. Anything whose layout the app does not know gets no link
    rather than a guessed one.
    """
    base, _, reference = workflow_uri.partition("@")
    slug = _github_slug(base)
    if slug is None:
        return None
    path = urlparse(base).path.removeprefix(f"/{slug}").strip("/")
    revision = claims.build_config_digest or _url_ref(reference)
    if not path or not revision:
        return None
    return f"https://github.com/{slug}/blob/{revision}/{path}"


def _describe_run(run_invocation_uri: str) -> str:
    """``run <id>, attempt <n>`` for a GitHub Actions run, else the URI itself.

    GitHub spells a run invocation as
    ``https://github.com/<owner>/<repo>/actions/runs/<id>/attempts/<n>``, whose
    repository part is already on the row above; what is left of it identifies
    the run, and the URI stays reachable through the link.
    """
    segments = urlparse(run_invocation_uri).path.split("/")
    described = [
        f"{word} {segments[segments.index(keyword) + 1]}"
        for keyword, word in (("runs", "run"), ("attempts", "attempt"))
        if keyword in segments and segments.index(keyword) + 1 < len(segments)
    ]
    return ", ".join(described) or run_invocation_uri


def _attestation_log_row(attestation: VerifiedAttestation) -> MetadataRow | None:
    """The transparency log entry the signature was recorded in."""
    if attestation.log_index is None:
        return None
    # Rekor states its tree id after the host, as in `rekor.sigstore.dev -
    # 1193050959916656506`, which identifies the log but is not needed to find
    # an entry in it.
    origin = attestation.log_origin
    host = origin.split()[0] if origin else None
    label = f"index {attestation.log_index}"
    if host is not None:
        label += f" on {host}"
    if host != "rekor.sigstore.dev":
        # Only the public good instance has a UI to send a user to, so an entry
        # in any other log gets no link rather than a guessed one.
        return ("Log entry", escape(label))
    return (
        "Log entry",
        format_clickable_link(
            escape(label),
            f"https://search.sigstore.dev/?logIndex={attestation.log_index}",
        ),
    )


# How much of the sidecar digest is shown before it is elided. Enough to
# recognize the one that was fetched, and short enough to keep the row readable.
SIDECAR_DIGEST_PREVIEW_LENGTH = 10


def _attestation_sidecar_value(url: str, bundle_index: int | None) -> str:
    """The sidecar as a link: its file name with the digest elided.

    The full URL is what the link points at, so it stays copyable; spelling it
    out in the row would wrap the whole block for a value whose channel and
    subdir are already on the metadata tab.
    """
    name = PurePosixPath(urlparse(url).path).name
    head, separator, digest = name.rpartition(".")
    if separator and len(digest) > SIDECAR_DIGEST_PREVIEW_LENGTH:
        name = f"{head}.{digest[:SIDECAR_DIGEST_PREVIEW_LENGTH]}…"
    value = format_clickable_link(escape(name), url)
    if bundle_index is not None:
        # Rattler counts the bundles of a sidecar from zero; the row reads as a
        # position, and says which of several bundles is the verified one.
        value += f" (bundle {bundle_index + 1})"
    return value


def build_attestation_row_groups(
    attestation: AttestationData,
) -> tuple[tuple[MetadataRow, ...], ...]:
    """Describe an attestation outcome as groups of detail rows.

    The first group is the provenance the signing certificate claims, which is
    what a person reads to decide whether the artifact came from where they
    expected. The second is what the signature itself is bound to, which is what
    a policy is pinned to and what an audit is carried out with. Absent values
    are left out, so a rejected or unsigned artifact collapses to the sidecar it
    would have been verified from, or to nothing at all.
    """
    verified = attestation.attestation
    build_rows: list[MetadataRow] = []
    binding_rows: list[MetadataRow] = []
    if verified is not None:
        if verified.claims is not None:
            build_rows.extend(_attestation_build_rows(verified.claims))
        # The certificate's own validity start, which is the signing time to
        # within the ten minutes a Fulcio certificate lives, and the log's
        # authenticated timestamp as the fallback when there was no certificate
        # to read it from.
        signed_at = verified.signed_at or verified.integrated_time
        if signed_at is not None:
            build_rows.append(("Signed at", escape(signed_at)))
        if verified.identity is not None:
            # The identity is a certificate SAN, not a page: a GitHub Actions
            # one spells out the workflow and ref
            # (`…/publish.yml@refs/heads/main`) and resolves to nothing when
            # opened. Shown verbatim rather than linked.
            binding_rows.append(("Identity", escape(verified.identity)))
        if verified.issuer is not None:
            binding_rows.append(("Issuer", escape(verified.issuer)))
        if verified.target_channel is not None:
            # CEP 27's binding: the channel the publisher signed for, which is
            # not necessarily the mirror the artifact was fetched from.
            binding_rows.append(("Channel", escape(verified.target_channel)))
        log_row = _attestation_log_row(verified)
        if log_row is not None:
            binding_rows.append(log_row)
    if attestation.sidecar_url is not None:
        binding_rows.append(
            (
                "Sidecar",
                _attestation_sidecar_value(
                    attestation.sidecar_url,
                    verified.index if verified is not None else None,
                ),
            )
        )
    return tuple(tuple(group) for group in (build_rows, binding_rows) if group)


def build_version_details_attestation_rows(
    attestation: AttestationData,
) -> tuple[MetadataRow, ...]:
    """The attestation tab: a verdict, the groups below it, the warnings.

    Every row shares one label column, across the blank lines between them, so
    the groups read as one block rather than as tables that happen to be
    adjacent. The verdict and those blank lines are rows without a label, which
    :func:`~pixi_browse.tui.widgets.render_detail_rows` lays out as lines of
    their own.
    """
    rows: list[MetadataRow] = [("", format_attestation_verdict(attestation))]
    for group in build_attestation_row_groups(attestation):
        rows.append(("", ""))
        rows.extend(group)
    if attestation.warnings:
        rows.append(("", ""))
        rows.extend(
            ("[yellow]Warning[/]", escape(warning)) for warning in attestation.warnings
        )
    return tuple(rows)


def format_version_details_run_exports(
    run_exports: RunExportsJson | None,
) -> tuple[str, ...]:
    return tuple(_format_run_exports_lines(run_exports))
