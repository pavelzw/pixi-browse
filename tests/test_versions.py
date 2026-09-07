import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest
from rattler.exceptions import InvalidMatchSpecError, InvalidPackageNameError
from rattler.match_spec import MatchSpec
from rattler.package import (
    IndexJson,
    NoArchLiteral,
    PackageName,
    PathsJson,
    RunExportsJson,
)
from rattler.package_streaming import PackageArchive
from rattler.platform import Platform
from rattler.repo_data import Dependent, Gateway, PackageRecord, RepoDataRecord
from rattler.version import Version
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from syrupy.assertion import SnapshotAssertion
from textual.app import App
from textual.widgets import OptionList, Static

from pixi_browse.__main__ import CondaMetadataTui, VersionEntry
from pixi_browse.models import (
    CompareFileRow,
    CompareRow,
    CompareSelection,
    PackageFile,
    RepodataPatchDiff,
    VersionArtifactData,
    VersionCompareData,
    ViewMode,
)
from pixi_browse.rendering import (
    build_repodata_patch_diff,
    build_version_artifact_data,
    build_version_compare_data,
    format_clickable_github_handle,
    format_clickable_url,
    format_version_details_metadata_lines,
    format_version_details_run_exports,
    render_package_preview,
    resolve_info_file_compare_rows,
    syntax_lexer_for_path,
)
from pixi_browse.repodata import (
    WhoNeedsQueryResult,
    query_whoneeds_records,
    whoneeds_target_label,
)
from pixi_browse.tui import (
    ACTIVE_SECTION_TITLE_STYLE,
    ACTIVE_TAB_STYLE,
    EMPTY_MATCHSPEC_RESULT,
    EMPTY_WHONEEDS_RESULT,
    INACTIVE_SECTION_TITLE_STYLE,
    INACTIVE_SELECTED_TAB_STYLE,
    INACTIVE_TAB_STYLE,
    Client,
    CompareDetailsView,
    CompareScreen,
    DetailSection,
    DownloadPathScreen,
    Empty,
    FileActionScreen,
    FilePreviewScreen,
    MainPanel,
    MatchSpecScreen,
    VersionDetailsView,
    WhoNeedsConfirmScreen,
    WhoNeedsScreen,
)
from pixi_browse.tui.state import AboutUrls
from pixi_browse.tui.version_loader import VersionDataLoader
from pixi_browse.tui.widgets import (
    DetailOptionList,
    FileActionOption,
    render_repodata_patches_body,
)


@dataclass(frozen=True)
class _Dependent:
    """Stand-in for `rattler.repo_data.Dependent`, which Python cannot build."""

    record: RepoDataRecord
    dependency: str


class _RecordingGateway:
    """Gateway stub that records the repodata caches it was asked to drop."""

    def __init__(self) -> None:
        self.cleared: list[str] = []
        self.queries: list[tuple[list[str], list[Platform], str | PackageRecord]] = []

    async def who_needs(
        self,
        *,
        sources: list[str],
        platforms: list[Platform],
        target: str | PackageRecord,
    ) -> list[Dependent]:
        self.queries.append((sources, platforms, target))
        return []

    def clear_repodata_cache(self, channel_name: str) -> None:
        self.cleared.append(channel_name)


def _make_artifact_data(
    *,
    metadata_rows: tuple[tuple[str, str], ...] = (("Meta", "meta"),),
    dependencies: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
    run_exports: RunExportsJson | None = None,
    file_paths: tuple[PackageFile, ...] = (),
    info_files: tuple[PackageFile, ...] = (),
) -> VersionArtifactData:
    return VersionArtifactData(
        metadata_rows=metadata_rows,
        dependencies=dependencies,
        constraints=constraints,
        file_paths=file_paths,
        info_files=info_files,
        run_exports=run_exports,
    )


def _make_repo_data_record(
    *,
    name: str = "demo",
    version: str = "1.2.3",
    build: str = "py313h123_0",
    build_number: int = 0,
    subdir: str = "noarch",
    file_name: str | None = None,
    channel: str = "https://conda.anaconda.org/conda-forge/",
    size: int = 2048,
    timestamp: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    license: str = "BSD-3-Clause",
    license_family: str = "BSD",
    arch: str | None = "x86_64",
    platform: str | None = "linux",
    noarch: NoArchLiteral | None = None,
    features: str | None = None,
    track_features: list[str] | None = None,
    python_site_packages_path: str | None = None,
    md5: bytes | None = bytes.fromhex("00112233445566778899aabbccddeeff"),
    sha256: bytes | None = bytes.fromhex(
        "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    ),
    legacy_bz2_md5: bytes | None = None,
    legacy_bz2_size: int | None = None,
    depends: list[str] | None = None,
    constrains: list[str] | None = None,
    extra_depends: dict[str, list[str]] | None = None,
    url: str | None = None,
) -> RepoDataRecord:
    resolved_file_name = file_name or f"{name}-{version}-{build}.conda"
    record = RepoDataRecord(
        package_record=PackageRecord(
            name=name,
            version=version,
            build=build,
            build_number=build_number,
            subdir=subdir,
            arch=arch,
            platform=platform,
            noarch=noarch,
            depends=depends,
            constrains=constrains,
            extra_depends=extra_depends,
            sha256=sha256,
            md5=md5,
            size=size,
            license=license,
            license_family=license_family,
            python_site_packages_path=python_site_packages_path,
            legacy_bz2_md5=legacy_bz2_md5,
            legacy_bz2_size=legacy_bz2_size,
        ),
        file_name=resolved_file_name,
        url=url or f"https://example.invalid/{resolved_file_name}",
        channel=channel,
    )
    record.timestamp = timestamp
    if features is not None:
        record.features = features
    if track_features is not None:
        record.track_features = track_features
    return record


class _FakeClickEvent:
    def __init__(self, style: Style | None = None) -> None:
        self.stopped = False
        self.style = style

    def stop(self) -> None:
        self.stopped = True


def test_query_whoneeds_records_groups_records_and_forwards_targets() -> None:
    python = _make_repo_data_record(name="python", version="3.13.1", build="h1_0")
    numpy = _make_repo_data_record(
        name="numpy",
        depends=["python >=3.10"],
        constrains=["python"],
    )
    legacy = _make_repo_data_record(name="legacy", depends=["python <3.10"])
    targets: list[str | PackageRecord] = []

    class _FakeGateway:
        async def who_needs(
            self,
            *,
            sources: list[str],
            platforms: list[Platform],
            target: str | PackageRecord,
        ) -> list[Dependent]:
            assert sources == ["conda-forge"]
            assert platforms == [Platform("linux-64"), Platform("noarch")]
            targets.append(target)
            return cast(
                list[Dependent],
                [
                    _Dependent(legacy, "python <3.10"),
                    _Dependent(numpy, "python >=3.10"),
                    # Rattler emits the same record again for its matching
                    # constrains edge; the UI must still show it only once.
                    _Dependent(numpy, "python"),
                ],
            )

    async def _query(target: str | PackageRecord) -> WhoNeedsQueryResult:
        return await query_whoneeds_records(
            gateway=cast(Gateway, _FakeGateway()),
            channel_name="conda-forge",
            platforms=[Platform("linux-64"), Platform("noarch")],
            target=target,
            log=lambda _message: None,
        )

    by_name = asyncio.run(_query("python"))
    by_record = asyncio.run(_query(python))

    expected = WhoNeedsQueryResult(
        package_names=["legacy", "numpy"],
        records_by_package={"legacy": [legacy], "numpy": [numpy]},
    )
    assert by_name == expected
    assert by_record == expected
    assert targets[0] == "python"
    assert targets[1] is python
    assert whoneeds_target_label(python) == "python 3.13.1 h1_0"


def test_build_version_entries_preserves_artifacts_per_build() -> None:
    app = CondaMetadataTui()
    records = [
        _make_repo_data_record(
            version="1.2.3",
            build="py313h123_0",
            build_number=0,
            subdir="noarch",
            file_name="demo-1.2.3-py313h123_0.conda",
        ),
        _make_repo_data_record(
            version="1.2.3",
            build="py313h123_0",
            build_number=0,
            subdir="noarch",
            file_name="demo-1.2.3-py313h123_0.tar.bz2",
        ),
    ]

    entries = app._build_version_entries(records)

    assert len(entries) == 2
    assert {entry.file_name for entry in entries} == {
        "demo-1.2.3-py313h123_0.conda",
        "demo-1.2.3-py313h123_0.tar.bz2",
    }


def _make_index_json(
    record: RepoDataRecord,
    *,
    depends: list[str] | None = None,
    constrains: list[str] | None = None,
    license: str | None = None,
    license_family: str | None = None,
    track_features: list[str] | None = None,
    subdir: str | None = "keep",
    noarch: NoArchLiteral | None = None,
    extra_depends: dict[str, list[str]] | None = None,
) -> IndexJson:
    """Build an ``info/index.json`` that matches ``record`` unless overridden."""
    data: dict[str, object] = {
        "name": record.name.source,
        "version": str(record.version),
        "build": record.build,
        "build_number": record.build_number,
        "depends": record.depends if depends is None else depends,
        "constrains": record.constrains if constrains is None else constrains,
        "license": record.license if license is None else license,
        "track_features": (
            record.track_features if track_features is None else track_features
        ),
        "arch": record.arch,
        "platform": record.platform,
    }
    if noarch is not None:
        data["noarch"] = noarch
    if extra_depends is not None:
        data["extra_depends"] = extra_depends
    resolved_license_family = (
        record.license_family if license_family is None else license_family
    )
    if resolved_license_family:
        data["license_family"] = resolved_license_family
    if subdir == "keep":
        data["subdir"] = record.subdir
    elif subdir is not None:
        data["subdir"] = subdir
    if record.timestamp is not None:
        data["timestamp"] = int(record.timestamp.timestamp() * 1000)
    return IndexJson.from_str(json.dumps(data))


def test_build_repodata_patch_diff_is_empty_when_repodata_matches_index_json() -> None:
    record = _make_repo_data_record(
        depends=["python >=3.13", "numpy"],
        constrains=["libdemo >=1"],
        track_features=["demo_feature"],
    )

    diff = build_repodata_patch_diff(record, _make_index_json(record))

    assert diff == RepodataPatchDiff()
    assert diff.is_patched is False
    assert diff.change_count == 0


def test_build_repodata_patch_diff_reports_patched_fields() -> None:
    record = _make_repo_data_record(
        depends=["python >=3.13", "numpy >=1.26,<2", "requests"],
        constrains=["libdemo >=1,<2"],
        license="MIT",
        track_features=["demo_feature"],
    )
    index_json = _make_index_json(
        record,
        depends=["python >=3.13", "numpy >=1.26", "scipy"],
        constrains=[],
        license="BSD-3-Clause",
        track_features=[],
    )

    diff = build_repodata_patch_diff(record, index_json)

    assert diff.metadata == (
        CompareRow(label="license", left="BSD-3-Clause", right="MIT", changed=True),
        CompareRow(label="track_features", left="", right="demo_feature", changed=True),
    )
    assert diff.dependencies == (
        CompareRow(
            label="depends",
            left="numpy >=1.26",
            right="numpy >=1.26,<2",
            changed=True,
        ),
        CompareRow(label="depends", left="scipy", right="", changed=True),
        CompareRow(label="depends", left="", right="requests", changed=True),
    )
    assert diff.constraints == (
        CompareRow(label="constrains", left="", right="libdemo >=1,<2", changed=True),
    )
    assert diff.is_patched is True
    assert diff.change_count == 6
    assert diff.rows == (*diff.metadata, *diff.dependencies, *diff.constraints)


def test_build_repodata_patch_diff_ignores_license_family() -> None:
    """conda-forge patches add ``license_family`` to nearly every record."""
    record = _make_repo_data_record(license_family="BSD")

    assert (
        build_repodata_patch_diff(record, _make_index_json(record, license_family=""))
        == RepodataPatchDiff()
    )


def test_build_repodata_patch_diff_ignores_arch_and_platform() -> None:
    """The indexer strips ``arch``/``platform`` from repodata; that is not a patch."""
    record = _make_repo_data_record(subdir="linux-64", arch=None, platform=None)
    index_json = IndexJson.from_str(
        json.dumps(
            {
                "name": "demo",
                "version": "1.2.3",
                "build": "py313h123_0",
                "build_number": 0,
                "subdir": "linux-64",
                "arch": "x86_64",
                "platform": "linux",
                "license": "BSD-3-Clause",
                "license_family": "BSD",
                "depends": [],
                "timestamp": int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000),
            }
        )
    )

    assert build_repodata_patch_diff(record, index_json) == RepodataPatchDiff()


def test_build_repodata_patch_diff_ignores_missing_subdir_in_index_json() -> None:
    record = _make_repo_data_record(subdir="linux-64")

    assert build_repodata_patch_diff(record, _make_index_json(record, subdir=None)) == (
        RepodataPatchDiff()
    )
    assert build_repodata_patch_diff(
        record, _make_index_json(record, subdir="noarch")
    ).metadata == (
        CompareRow(label="subdir", left="noarch", right="linux-64", changed=True),
    )


def test_build_repodata_patch_diff_reports_patched_noarch_and_extra_depends() -> None:
    """Patches can change the noarch kind and the per-extra dependency groups."""
    record = _make_repo_data_record(
        noarch="python",
        extra_depends={"plot": ["matplotlib >=3.8"], "test": ["pytest"]},
    )
    index_json = _make_index_json(
        record,
        noarch="generic",
        extra_depends={"plot": ["matplotlib"], "docs": ["sphinx"]},
    )

    diff = build_repodata_patch_diff(record, index_json)

    assert diff.metadata == (
        CompareRow(label="noarch", left="generic", right="python", changed=True),
    )
    assert diff.dependencies == (
        CompareRow(label="extra_depends[docs]", left="sphinx", right="", changed=True),
        CompareRow(
            label="extra_depends[plot]",
            left="matplotlib",
            right="matplotlib >=3.8",
            changed=True,
        ),
        CompareRow(label="extra_depends[test]", left="", right="pytest", changed=True),
    )
    assert diff.constraints == ()


def test_render_repodata_patches_body_shows_unpatched_and_patched_columns() -> None:
    table = cast(
        Table,
        render_repodata_patches_body(
            RepodataPatchDiff(
                metadata=(
                    CompareRow(label="license", left="BSD", right="MIT", changed=True),
                ),
                dependencies=(
                    CompareRow(
                        label="depends", left="", right="requests", changed=True
                    ),
                ),
            )
        ),
    )

    assert table.columns[0].header == "Field"
    assert table.columns[1].header == "Unpatched (index.json)"
    assert table.columns[2].header == "Patched (repodata)"
    assert table.columns[0]._cells == ["license", "depends"]
    assert cast(Text, table.columns[1]._cells[0]).plain == "BSD"
    assert cast(Text, table.columns[1]._cells[0]).style == "red"
    assert cast(Text, table.columns[2]._cells[0]).plain == "MIT"
    assert cast(Text, table.columns[2]._cells[0]).style == "green"
    assert cast(Text, table.columns[2]._cells[1]).plain == "requests"


def test_render_repodata_patches_body_explains_empty_state() -> None:
    unpatched = cast(Text, render_repodata_patches_body(RepodataPatchDiff()))

    assert unpatched.plain == "No repodata patches."
    assert unpatched.style == "dim"


def test_build_version_artifact_data_includes_package_paths() -> None:
    record = _make_repo_data_record(
        version="1.2.3",
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
        depends=["python >=3.13"],
    )

    details = build_version_artifact_data(
        "demo",
        record,
        package_paths=(
            PackageFile("bin/demo"),
            PackageFile("lib/python3.13/site-packages/demo.py"),
        ),
        info_files=(PackageFile("info/index.json"), PackageFile("info/paths.json")),
    )

    assert details.file_paths == (
        PackageFile("bin/demo"),
        PackageFile("lib/python3.13/site-packages/demo.py"),
    )
    assert details.info_files == (
        PackageFile("info/index.json"),
        PackageFile("info/paths.json"),
    )


def test_format_version_details_metadata_lines_aligns_metadata_rows() -> None:
    record = _make_repo_data_record(
        version="1.2.3",
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )

    details = build_version_artifact_data(
        "demo",
        record,
        repository_urls=["https://github.com/example/demo"],
        documentation_urls=["https://docs.example.com/demo"],
    )
    metadata_lines = format_version_details_metadata_lines(details)

    assert "Package               demo" in metadata_lines
    assert "Python Site-Packages  not available" in metadata_lines
    assert any(
        line.startswith("Repository            [@click=app.open_external_url(")
        for line in metadata_lines
    )
    assert (
        "Built with            rattler-build 0.47.0"
        in format_version_details_metadata_lines(
            build_version_artifact_data(
                "demo",
                record,
                rattler_build_version="0.47.0",
            )
        )
    )


def test_format_version_details_run_exports_from_py_rattler() -> None:
    record = _make_repo_data_record(
        version="1.2.3",
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )

    details = build_version_artifact_data(
        "demo",
        record,
        run_exports=RunExportsJson(
            weak=["python_abi 3.13.* *_cp313"],
            strong=["libdemo >=1.2.3"],
            noarch=["python"],
        ),
    )

    assert format_version_details_run_exports(details.run_exports) == (
        "weak: python_abi 3.13.* *_cp313",
        "strong: libdemo >=1.2.3",
        "noarch: python",
    )
    assert details.dependencies == ()
    assert details.constraints == ()
    assert len(format_version_details_run_exports(details.run_exports)) == 3


def test_build_version_compare_data_reports_metadata_dependency_and_file_changes() -> (
    None
):
    left_record = _make_repo_data_record(
        version="1.2.3",
        build="py313h123_0",
        file_name="demo-1.2.3-py313h123_0.conda",
        depends=["python >=3.12", "numpy >=1.0"],
        constrains=["libdemo >=1"],
    )
    right_record = _make_repo_data_record(
        version="1.2.4",
        build="py313h456_0",
        file_name="demo-1.2.4-py313h456_0.conda",
        depends=["python >=3.13", "pydantic >=2"],
        constrains=[],
    )

    left_artifact = build_version_artifact_data(
        "demo",
        left_record,
        package_paths=(
            PackageFile(
                "bin/demo",
                1234,
                bytes.fromhex("00" * 32),
                False,
                "hardlink",
            ),
            PackageFile(
                "info/about.json",
                256,
                bytes.fromhex("11" * 32),
                False,
                "hardlink",
            ),
        ),
        run_exports=RunExportsJson(weak=["python_abi 3.12.* *_cp312"]),
    )
    right_artifact = build_version_artifact_data(
        "demo",
        right_record,
        package_paths=(
            PackageFile(
                "bin/demo",
                2048,
                bytes.fromhex("22" * 32),
                False,
                "hardlink",
            ),
            PackageFile(
                "lib/demo.py",
                512,
                bytes.fromhex("33" * 32),
                False,
                "hardlink",
            ),
        ),
        run_exports=RunExportsJson(weak=["python_abi 3.13.* *_cp313"]),
    )

    compare_data = build_version_compare_data(
        CompareSelection(
            "demo",
            VersionEntry(
                version=Version("1.2.3"),
                build="py313h123_0",
                build_number=0,
                subdir="noarch",
                file_name="demo-1.2.3-py313h123_0.conda",
            ),
        ),
        left_artifact,
        CompareSelection(
            "demo",
            VersionEntry(
                version=Version("1.2.4"),
                build="py313h456_0",
                build_number=0,
                subdir="noarch",
                file_name="demo-1.2.4-py313h456_0.conda",
            ),
        ),
        right_artifact,
    )

    version_row = next(
        row for row in compare_data.metadata_rows if row.label == "Version"
    )
    assert version_row.left == "1.2.3"
    assert version_row.right == "1.2.4"
    assert version_row.changed is True

    python_row = next(row for row in compare_data.dependencies if row.label == "python")
    assert python_row.left == "python >=3.12"
    assert python_row.right == "python >=3.13"
    assert python_row.changed is True

    numpy_row = next(row for row in compare_data.dependencies if row.label == "numpy")
    assert numpy_row.left == "numpy >=1.0"
    assert numpy_row.right == ""
    assert numpy_row.changed is True

    pydantic_row = next(
        row for row in compare_data.dependencies if row.label == "pydantic"
    )
    assert pydantic_row.left == ""
    assert pydantic_row.right == "pydantic >=2"
    assert pydantic_row.changed is True

    bin_demo_row = next(row for row in compare_data.files if row.label == "bin/demo")
    assert "1.2 KiB" in bin_demo_row.left
    assert "2.0 KiB" in bin_demo_row.right
    assert bin_demo_row.changed is True

    about_row = next(
        row for row in compare_data.files if row.label == "info/about.json"
    )
    assert about_row.left.startswith("info/about.json")
    assert about_row.right == ""
    assert about_row.changed is True

    lib_demo_row = next(row for row in compare_data.files if row.label == "lib/demo.py")
    assert lib_demo_row.left == ""
    assert lib_demo_row.right.startswith("lib/demo.py")
    assert lib_demo_row.changed is True


def test_build_version_compare_data_ignores_missing_optional_file_metadata() -> None:
    record = _make_repo_data_record(
        version="1.2.3",
        build="py313h123_0",
        file_name="demo-1.2.3-py313h123_0.conda",
    )

    left_artifact = build_version_artifact_data(
        "demo",
        record,
        package_paths=(PackageFile("bin/demo", size_in_bytes=None),),
    )
    right_artifact = build_version_artifact_data(
        "demo",
        record,
        package_paths=(PackageFile("bin/demo", size_in_bytes=1234),),
    )

    compare_data = build_version_compare_data(
        CompareSelection(
            "demo",
            VersionEntry(
                version=Version("1.2.3"),
                build="py313h123_0",
                build_number=0,
                subdir="noarch",
                file_name="demo-1.2.3-py313h123_0.conda",
            ),
        ),
        left_artifact,
        CompareSelection(
            "demo",
            VersionEntry(
                version=Version("1.2.3"),
                build="py313h123_0",
                build_number=0,
                subdir="noarch",
                file_name="demo-1.2.3-py313h123_0.conda",
            ),
        ),
        right_artifact,
    )

    file_row = next(row for row in compare_data.files if row.label == "bin/demo")
    assert file_row.changed is False


def _make_compare_selection(record: RepoDataRecord) -> CompareSelection:
    return CompareSelection(
        record.name.normalized,
        VersionEntry(
            version=record.version,
            build=record.build,
            build_number=record.build_number,
            subdir=record.subdir,
            file_name=record.file_name,
        ),
    )


def test_build_version_compare_data_keeps_unparsable_dependency_lines() -> None:
    """Lines that are not valid MatchSpecs cannot be grouped by package name, so
    they are matched verbatim after the parsable ones."""
    record = _make_repo_data_record()
    left_artifact = build_version_artifact_data(
        "demo",
        _make_repo_data_record(depends=["numpy[", "python >=3.13", "broken ["]),
    )
    right_artifact = build_version_artifact_data(
        "demo",
        _make_repo_data_record(depends=["python >=3.14", "numpy[", "also ["]),
    )

    compare_data = build_version_compare_data(
        _make_compare_selection(record),
        left_artifact,
        _make_compare_selection(record),
        right_artifact,
    )

    assert compare_data.dependencies == (
        CompareRow(
            label="python", left="python >=3.13", right="python >=3.14", changed=True
        ),
        CompareRow(label="numpy[", left="numpy[", right="numpy[", changed=False),
        CompareRow(label="broken [", left="broken [", right="", changed=True),
        CompareRow(label="also [", left="", right="also [", changed=True),
    )


def test_build_version_compare_data_flags_symlink_changes() -> None:
    """A file that turns into a symlink, or a symlink that changes its target,
    counts as changed even when sizes and hashes are unavailable."""
    record = _make_repo_data_record()
    left_artifact = build_version_artifact_data(
        "demo",
        record,
        package_paths=(
            PackageFile("lib/libdemo.so", path_type="softlink", link_target="a.so"),
            PackageFile("lib/libdemo.so.1", path_type="softlink", link_target="x"),
            PackageFile("lib/libdemo.so.2", path_type="softlink", link_target="y"),
        ),
    )
    right_artifact = build_version_artifact_data(
        "demo",
        record,
        package_paths=(
            PackageFile("lib/libdemo.so", size_in_bytes=1234, path_type="hardlink"),
            PackageFile("lib/libdemo.so.1", path_type="softlink", link_target="x"),
            PackageFile("lib/libdemo.so.2", path_type="softlink", link_target="z"),
        ),
    )

    compare_data = build_version_compare_data(
        _make_compare_selection(record),
        left_artifact,
        _make_compare_selection(record),
        right_artifact,
    )

    assert [(row.label, row.changed) for row in compare_data.files] == [
        ("lib/libdemo.so", True),
        ("lib/libdemo.so.1", False),
        ("lib/libdemo.so.2", True),
    ]


def test_build_version_compare_data_uses_sizes_for_initial_info_status() -> None:
    record = _make_repo_data_record()
    selection = CompareSelection(
        "demo",
        VersionEntry(
            version=Version("1.2.3"),
            build="py313h123_0",
            build_number=0,
            subdir="noarch",
            file_name=record.file_name,
        ),
    )
    left_artifact = build_version_artifact_data(
        "demo",
        record,
        info_files=(
            PackageFile("info/index.json", 100),
            PackageFile("info/size-diff.json", 400),
            PackageFile("info/left-only.json", 200),
        ),
    )
    right_artifact = build_version_artifact_data(
        "demo",
        record,
        info_files=(
            PackageFile("info/index.json", 100),
            PackageFile("info/size-diff.json", 500),
            PackageFile("info/right-only.json", 300),
        ),
    )

    compare_data = build_version_compare_data(
        selection,
        left_artifact,
        selection,
        right_artifact,
    )

    assert [row.label for row in compare_data.info_files] == [
        "index.json",
        "size-diff.json",
        "left-only.json",
        "right-only.json",
    ]
    assert compare_data.info_files[0].comparison_known is False
    assert compare_data.info_files[0].left_file == PackageFile("info/index.json", 100)
    assert compare_data.info_files[0].right_file == PackageFile("info/index.json", 100)
    assert compare_data.info_files[0].left == "info/index.json (100 B)"
    assert compare_data.info_files[0].right == "info/index.json (100 B)"
    assert compare_data.info_files[1].comparison_known is True
    assert compare_data.info_files[1].changed is True
    assert CompareDetailsView._compare_file_prefix(compare_data.info_files[1]) == "~ "
    assert compare_data.info_files[2].comparison_known is True
    assert compare_data.info_files[3].comparison_known is True


def test_resolve_info_file_compare_rows_uses_lazy_sha256_values() -> None:
    unknown_rows = (
        CompareFileRow(
            label="same.json",
            left="info/same.json",
            right="info/same.json",
            changed=False,
            left_file=PackageFile("info/same.json", 100),
            right_file=PackageFile("info/same.json", 100),
            comparison_known=False,
        ),
        CompareFileRow(
            label="changed.json",
            left="info/changed.json",
            right="info/changed.json",
            changed=False,
            left_file=PackageFile("info/changed.json", 200),
            right_file=PackageFile("info/changed.json", 200),
            comparison_known=False,
        ),
    )

    rows = resolve_info_file_compare_rows(
        unknown_rows,
        left_sha256={
            "info/same.json": bytes.fromhex("11" * 32),
            "info/changed.json": bytes.fromhex("22" * 32),
        },
        right_sha256={
            "info/same.json": bytes.fromhex("11" * 32),
            "info/changed.json": bytes.fromhex("33" * 32),
        },
    )

    assert rows[0].comparison_known is True
    assert rows[0].changed is False
    assert rows[0].left_file is not None
    assert rows[0].left_file.size_in_bytes == 100
    assert rows[1].comparison_known is True
    assert rows[1].changed is True
    assert rows[1].right_file is not None
    assert rows[1].right_file.size_in_bytes == 200


def test_format_version_details_metadata_lines_include_about_urls() -> None:
    record = _make_repo_data_record(
        version="1.2.3",
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )

    details = build_version_artifact_data(
        "demo",
        record,
        repository_urls=["https://github.com/example/demo"],
        documentation_urls=["https://docs.example.com/demo"],
        homepage_urls=["https://example.com/demo"],
        recipe_maintainers=["@pavelzw", "xhochy"],
        provenance_remote_url="https://github.com/conda-forge/polars-feedstock.git",
        provenance_sha="f48623bd7b6d92b6573f21a907a62c8e06b75c5c",
        rattler_build_version="0.38.0",
    )
    metadata_lines = format_version_details_metadata_lines(details)

    assert any(
        line.startswith("Package URL")
        and "https://example.invalid/demo-1.2.3-py313h123_0.conda" in line
        for line in metadata_lines
    )
    assert any(
        line.startswith("Repository") and "https://github.com/example/demo" in line
        for line in metadata_lines
    )
    assert any(
        line.startswith("Documentation") and "https://docs.example.com/demo" in line
        for line in metadata_lines
    )
    assert any(
        line.startswith("Homepage") and "https://example.com/demo" in line
        for line in metadata_lines
    )
    assert any(
        line.startswith("Recipe maintainers")
        and "@click=app.open_external_url('https://github.com/pavelzw')" in line
        and "@click=app.open_external_url('https://github.com/xhochy')" in line
        for line in metadata_lines
    )
    assert any(
        line.startswith("Provenance")
        and "https://github.com/conda-forge/polars-feedstock/commit/f48623bd7b6d92b6573f21a907a62c8e06b75c5c"
        in line
        and "conda-forge/polars-feedstock@f48623bd7b6d92b6573f21a907a62c8e06b75c5c"
        in line
        for line in metadata_lines
    )
    assert any(
        line.startswith("Built with") and "rattler-build 0.38.0" in line
        for line in metadata_lines
    )


def test_format_version_details_metadata_lines_for_a_tiny_legacy_record(
    snapshot: SnapshotAssertion,
) -> None:
    """Sub-kibibyte sizes are shown in bytes, list fields are comma separated
    and a non-GitHub provenance stays a plain link to the remote."""
    record = _make_repo_data_record(
        size=512,
        legacy_bz2_size=900,
        legacy_bz2_md5=bytes.fromhex("ffeeddccbbaa99887766554433221100"),
        track_features=["nomkl", "debug"],
        features="nomkl",
        python_site_packages_path="lib/python3.13/site-packages",
    )

    details = build_version_artifact_data(
        "demo",
        record,
        provenance_remote_url="https://gitlab.com/example/demo-feedstock.git",
        provenance_sha="0123456789abcdef",
    )

    assert format_version_details_metadata_lines(details) == snapshot


def test_format_clickable_url_uses_textual_click_action() -> None:
    rendered = format_clickable_url("https://example.com/demo")

    assert (
        rendered
        == "[@click=app.open_external_url('https://example.com/demo')]https://example.com/demo[/]"
    )


def test_format_clickable_github_handle_uses_github_profile() -> None:
    rendered = format_clickable_github_handle("@pavelzw")

    assert (
        rendered
        == "[@click=app.open_external_url('https://github.com/pavelzw')]@pavelzw[/]"
    )


def test_render_package_preview_shows_version_selector_preview() -> None:
    records = [
        _make_repo_data_record(
            version="1.2.3",
            build="py313h123_1",
            build_number=1,
            subdir="linux-64",
            file_name="demo-1.2.3-py313h123_1.conda",
        ),
        _make_repo_data_record(
            version="1.2.2",
            build="py313h123_0",
            build_number=0,
            subdir="noarch",
            file_name="demo-1.2.2-py313h123_0.conda",
        ),
    ]

    rendered = render_package_preview("demo", records)

    assert "Version selector preview" in rendered
    assert "Press Enter to open the version list." in rendered
    assert "▾ linux-64 (1)" in rendered
    assert "▾ noarch (1)" in rendered
    assert "1.2.3" in rendered
    assert "py313h123_1" in rendered
    assert "URL" not in rendered
    assert "Dependencies" not in rendered


def test_render_package_preview_without_records_explains_the_empty_state() -> None:
    assert render_package_preview("demo", []) == "# demo\n\nNo metadata records found."


def test_render_package_preview_orders_subdirs_by_latest_version_then_name() -> None:
    records = [
        _make_repo_data_record(version="1.33.1", subdir="osx-arm64"),
        _make_repo_data_record(version="1.33.1", subdir="linux-64"),
        _make_repo_data_record(version="1.34.0", subdir="noarch"),
    ]

    rendered = render_package_preview("demo", records)

    headings = [
        rendered.index("▾ noarch"),
        rendered.index("▾ linux-64"),
        rendered.index("▾ osx-arm64"),
    ]
    assert headings == sorted(headings)


def test_get_package_paths_caches_archive_paths() -> None:
    """``paths.json`` entries of every path type become ``PackageFile``s; symlink
    targets are read from the archive once and the result is cached."""
    loader = VersionDataLoader(client=cast(Client, object()))
    preview_key = ("demo", "1.2.3", "py313h123_0", 0, "noarch", "demo.conda")
    calls: list[str] = []
    paths_json = PathsJson.from_str(
        json.dumps(
            {
                "paths_version": 1,
                "paths": [
                    {
                        "_path": "bin/demo",
                        "path_type": "hardlink",
                        "size_in_bytes": 1234,
                        "sha256": "00" * 32,
                    },
                    {
                        "_path": "lib/python3.13/site-packages/demo.py",
                        "path_type": "softlink",
                        "no_link": True,
                    },
                    {"_path": "etc/conda/activate.d", "path_type": "directory"},
                ],
            }
        )
    )

    class _FakeEntry:
        name = "lib/python3.13/site-packages/demo.py"
        is_symlink = True
        link_target = "demo.py"

    class _FakeArchive:
        async def paths_json(self) -> PathsJson:
            calls.append("paths_json")
            return paths_json

        async def stream(self, section: str):
            calls.append(section)
            yield _FakeEntry()

    archive = cast(PackageArchive, _FakeArchive())
    paths = asyncio.run(loader.get_package_paths(preview_key, archive))
    cached_paths = asyncio.run(loader.get_package_paths(preview_key, archive))

    assert paths == [
        PackageFile(
            "bin/demo",
            1234,
            bytes.fromhex("00" * 32),
            False,
            "hardlink",
        ),
        PackageFile(
            "lib/python3.13/site-packages/demo.py",
            None,
            None,
            True,
            "softlink",
            "demo.py",
        ),
        PackageFile("etc/conda/activate.d", None, None, False, "directory"),
    ]
    assert cached_paths == paths
    assert calls == ["paths_json", "pkg"]


def test_get_info_files_streams_archive_info_section_with_sizes() -> None:
    loader = VersionDataLoader(client=cast(Client, object()))
    calls: list[str] = []

    class _FakeEntry:
        def __init__(
            self,
            name: str,
            size: int,
            *,
            is_file: bool = True,
            link_target: str | None = None,
        ) -> None:
            self.name = name
            self.size = size
            self.is_file = is_file
            self.is_symlink = link_target is not None
            self.link_target = link_target

    class _FakeArchive:
        async def stream(self, section: str):
            calls.append(section)
            for entry in (
                _FakeEntry("info/index.json", 42),
                _FakeEntry("info/recipe", 0, is_file=False),
                _FakeEntry(
                    "info/current", 13, is_file=False, link_target="recipe/meta.yaml"
                ),
                _FakeEntry("info/paths.json", 1234),
            ):
                yield entry

    archive = cast(PackageArchive, _FakeArchive())
    files = asyncio.run(loader.get_info_files(archive))

    assert files == [
        PackageFile("info/index.json", 42),
        PackageFile(
            path="info/current",
            size_in_bytes=13,
            path_type="softlink",
            link_target="recipe/meta.yaml",
        ),
        PackageFile("info/paths.json", 1234),
    ]
    assert calls == ["info"]


def test_get_about_urls_caches_archive_about_json() -> None:
    loader = VersionDataLoader(client=cast(Client, object()))
    preview_key = ("demo", "1.2.3", "py313h123_0", 0, "noarch", "demo.conda")
    calls: list[str] = []

    class _FakeAboutJson:
        dev_url = ["https://github.com/example/demo"]
        doc_url = ["https://docs.example.com/demo"]
        home = ["https://example.com/demo"]
        extra = {
            "recipe-maintainers": ["@pavelzw", "xhochy"],
            "remote_url": "https://github.com/conda-forge/polars-feedstock.git",
            "sha": "f48623bd7b6d92b6573f21a907a62c8e06b75c5c",
        }

    class _FakeArchive:
        async def about_json(self) -> _FakeAboutJson:
            calls.append("about_json")
            return _FakeAboutJson()

        async def read_file(self, path: str) -> bytes:
            calls.append(path)
            return b"system_tools:\n  rattler-build: 0.38.0\n"

    archive = cast(PackageArchive, _FakeArchive())
    about_urls = asyncio.run(loader.get_about_urls(preview_key, archive))
    cached_about_urls = asyncio.run(loader.get_about_urls(preview_key, archive))

    assert about_urls == AboutUrls(
        repository=("https://github.com/example/demo",),
        documentation=("https://docs.example.com/demo",),
        homepage=("https://example.com/demo",),
        recipe_maintainers=("@pavelzw", "xhochy"),
        provenance_remote_url="https://github.com/conda-forge/polars-feedstock.git",
        provenance_sha="f48623bd7b6d92b6573f21a907a62c8e06b75c5c",
        rattler_build_version="0.38.0",
    )
    assert cached_about_urls == about_urls
    assert calls == ["about_json", "info/recipe/rendered_recipe.yaml"]


@pytest.mark.parametrize(
    ("recipe_maintainers", "expected"),
    [
        ("pavelzw", ("pavelzw",)),
        (["@pavelzw", 42, "xhochy"], ("@pavelzw", "xhochy")),
        ({"not": "a list"}, ()),
    ],
    ids=["single-string", "list-with-non-string", "mapping"],
)
def test_get_about_urls_normalizes_recipe_maintainers(
    recipe_maintainers: object, expected: tuple[str, ...]
) -> None:
    """``extra.recipe-maintainers`` is free-form JSON; only strings survive."""
    loader = VersionDataLoader(client=cast(Client, object()))
    preview_key = ("demo", "1.2.3", "py313h123_0", 0, "noarch", "demo.conda")

    class _FakeAboutJson:
        dev_url: list[str] = []
        doc_url: list[str] = []
        home: list[str] = []
        extra = {"recipe-maintainers": recipe_maintainers}

    class _FakeArchive:
        async def about_json(self) -> _FakeAboutJson:
            return _FakeAboutJson()

        async def read_file(self, path: str) -> bytes | None:
            return None

    archive = cast(PackageArchive, _FakeArchive())
    about_urls = asyncio.run(loader.get_about_urls(preview_key, archive))

    assert about_urls == AboutUrls(recipe_maintainers=expected)


def test_extract_rattler_build_version_from_rendered_recipe() -> None:
    rendered_recipe = """
context:
  some-value: true
system_tools:
  rattler-build: 0.38.0
  micromamba: 2.3.2
package:
  name: demo
"""

    assert VersionDataLoader.extract_rattler_build_version(rendered_recipe) == "0.38.0"


@pytest.mark.parametrize(
    "rendered_recipe",
    [
        "- just\n- a list\n",
        "package:\n  name: demo\n",
        "system_tools: not-a-mapping\n",
        "system_tools:\n  micromamba: 2.3.2\n",
    ],
    ids=["not-a-mapping", "no-system-tools", "system-tools-scalar", "no-rattler-build"],
)
def test_extract_rattler_build_version_without_rattler_build(
    rendered_recipe: str,
) -> None:
    assert VersionDataLoader.extract_rattler_build_version(rendered_recipe) is None


def test_ensure_available_platforms_removes_unavailable_selected_platforms() -> None:
    app = CondaMetadataTui(default_platforms={Platform("linux-64"), Platform("osx-64")})
    app._available_platform_names = [Platform("linux-64"), Platform("noarch")]

    asyncio.run(app._ensure_available_platforms())

    assert app._selected_platform_names == {Platform("linux-64")}


def test_ensure_available_platforms_falls_back_to_default_when_needed() -> None:
    app = CondaMetadataTui(default_platforms={Platform("osx-64")})
    app._available_platform_names = [Platform("linux-64"), Platform("noarch")]

    asyncio.run(app._ensure_available_platforms())

    assert app._selected_platform_names == {Platform("linux-64"), Platform("noarch")}


def test_page_step_uses_visible_height() -> None:
    assert MainPanel._page_step(20) == 20
    assert MainPanel._page_step(7) == 7
    assert MainPanel._page_step(0) == 1


def test_help_text_includes_expected_keybinds() -> None:
    app = CondaMetadataTui()

    help_text = app._help_text()

    assert "?                 Show this help" in help_text
    assert "j / k             Move selection or scroll" in help_text
    assert "h / l             Focus left / right pane" in help_text
    assert "1 / 2 / 3         Focus metadata, deps, or files" in help_text
    assert "Tab / Shift+Tab" in help_text
    assert "Cycle focused section" in help_text
    assert "x                 Swap compare left / right" in help_text
    assert "[ / ]             Cycle section tabs" in help_text
    assert "Ctrl+u / Ctrl+d   Page up / down" in help_text
    assert "m                 Query MatchSpec" in help_text
    assert "w                 Query reverse dependencies" in help_text


def test_matchspec_screen_validates_input() -> None:
    empty = MatchSpecScreen.validate_matchspec("")
    numpy = MatchSpecScreen.validate_matchspec("numpy >=2")
    glob = MatchSpecScreen.validate_matchspec("python*")

    assert isinstance(empty, Empty)
    assert empty == EMPTY_MATCHSPEC_RESULT
    assert isinstance(numpy, MatchSpec)
    assert str(numpy).startswith("numpy")
    assert isinstance(glob, MatchSpec)
    assert str(glob) == "python*"

    with pytest.raises(InvalidMatchSpecError):
        MatchSpecScreen.validate_matchspec("numpy[")


def test_whoneeds_screen_validates_package_name() -> None:
    package_name = WhoNeedsScreen.validate_package_name(" NumPy ")
    empty = WhoNeedsScreen.validate_package_name("   ")

    assert isinstance(package_name, PackageName)
    assert package_name.normalized == "numpy"
    assert isinstance(empty, Empty)
    assert empty == EMPTY_WHONEEDS_RESULT

    with pytest.raises(InvalidPackageNameError):
        WhoNeedsScreen.validate_package_name("numpy >=2")


@pytest.mark.parametrize(
    ("mode", "selected_package", "target", "expected"),
    [
        ("packages", None, None, ""),
        ("versions", "demo", None, "demo"),
        ("packages", None, "python", "python"),
    ],
)
def test_whoneeds_screen_initial_value(
    mode: ViewMode,
    selected_package: str | None,
    target: str | None,
    expected: str,
) -> None:
    app = CondaMetadataTui()
    app._mode = mode
    app._selected_package = selected_package
    app._whoneeds_target = target

    assert app._whoneeds_screen_initial_value() == expected


@pytest.mark.parametrize(
    ("mode", "matchspec_query", "target", "expected"),
    [
        ("packages", "", None, "[0] Packages"),
        ("platforms", "numpy >=2", None, "[0] Platforms"),
        ("packages", "numpy >=2", None, "[0] MatchSpec: numpy >=2"),
        ("packages", "", "python", "[0] Who needs: python"),
    ],
)
def test_sidebar_title_names_active_query(
    mode: ViewMode,
    matchspec_query: str,
    target: str | None,
    expected: str,
) -> None:
    app = CondaMetadataTui()
    app._mode = mode
    app._matchspec_query = matchspec_query
    app._whoneeds_target = target

    assert app._sidebar_title_text(selected=False).plain == expected


def test_sidebar_title_shows_whoneeds_record_target() -> None:
    app = CondaMetadataTui()
    app._whoneeds_target = _make_repo_data_record(
        version="1.2.3",
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )

    assert (
        app._sidebar_title_text(selected=False).plain
        == "[0] Who needs: demo 1.2.3 py313h123_0"
    )


def test_whoneeds_confirm_screen_reports_the_chosen_action() -> None:
    class _HostApp(App[None]):
        pass

    results: list[str | None] = []

    async def _run() -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(
                WhoNeedsConfirmScreen("demo 1.2.3 py313h123_0 [noarch]"),
                results.append,
            )
            await pilot.pause()
            screen = cast(WhoNeedsConfirmScreen, app.screen)
            option_list = screen.query_one("#whoneeds-confirm-list", OptionList)

            assert option_list.has_focus
            assert [
                str(option_list.get_option_at_index(index).prompt)
                for index in range(option_list.option_count)
            ] == ["Run the query", "Query something else", "Cancel"]
            assert (
                screen.query_one("#whoneeds-confirm-target", Static).content
                == "demo 1.2.3 py313h123_0 [noarch]"
            )

            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(_run())

    assert results == ["run"]


def test_dependency_header_tabs_are_clickable() -> None:
    text = VersionDetailsView._render_clickable_dependency_tab(
        "constraints",
        "Constraints (1)",
        active=False,
        pane_active=False,
    )

    assert text.plain == "Constraints (1)"
    assert any(
        span.style.meta == {"@click": ("select_dependency_tab", ("constraints",))}
        for span in text.spans
        if isinstance(span.style, Style)
    )
    assert any(
        span.style == INACTIVE_TAB_STYLE
        for span in text.spans
        if isinstance(span.style, Style)
        and span.style.meta != {"@click": ("select_dependency_tab", ("constraints",))}
    )


def test_selected_dependency_tab_is_not_bold_when_pane_is_inactive() -> None:
    text = VersionDetailsView._render_clickable_dependency_tab(
        "constraints",
        "Constraints (1)",
        active=True,
        pane_active=False,
    )

    assert any(
        span.style.bold is False
        for span in text.spans
        if isinstance(span.style, Style)
        and span.style.meta != {"@click": ("select_dependency_tab", ("constraints",))}
    )


def test_compare_details_view_uses_detail_sections_with_selected_pane_class() -> None:
    compare_data = VersionCompareData(
        left_selection=CompareSelection(
            "demo",
            VersionEntry(
                version=Version("1.0.0"),
                build="py313h123_0",
                build_number=0,
                subdir="noarch",
                file_name="demo-1.0.0-py313h123_0.conda",
            ),
        ),
        right_selection=CompareSelection(
            "demo",
            VersionEntry(
                version=Version("1.0.1"),
                build="py313h123_0",
                build_number=0,
                subdir="noarch",
                file_name="demo-1.0.1-py313h123_0.conda",
            ),
        ),
        metadata_rows=(),
        dependencies=(),
        constraints=(),
        run_exports=(),
        files=(),
    )
    view = CompareDetailsView(compare_data)
    sections = list(view.compose())

    assert "-pane-selected" in view.classes
    assert len(sections) == 3
    assert all(isinstance(section, DetailSection) for section in sections)


def _patched_artifact_data(change_count: int = 2) -> VersionArtifactData:
    rows = tuple(
        CompareRow(label="depends", left="", right=f"dep{index}", changed=True)
        for index in range(change_count)
    )
    return VersionArtifactData(
        metadata_rows=(("Meta", "meta"),),
        dependencies=(),
        constraints=(),
        repodata_patches=RepodataPatchDiff(dependencies=rows),
    )


def test_metadata_header_always_shows_patches_tab_with_count() -> None:
    view = VersionDetailsView()
    view._pane_selected = True
    view._active_section = 0

    assert view._render_metadata_header().plain == "[1] Metadata - Repodata patches"

    view._details = _make_artifact_data()
    assert view._render_metadata_header().plain == "[1] Metadata - Repodata patches (0)"

    view._details = _patched_artifact_data(2)
    header = view._render_metadata_header()
    assert header.plain == "[1] Metadata - Repodata patches (2)"
    assert any(
        span.style == ACTIVE_TAB_STYLE
        and header.plain[span.start : span.end] == "Metadata"
        for span in header.spans
        if isinstance(span.style, Style)
    )
    assert any(
        isinstance(span.style, Style)
        and span.style.meta.get("@click") == ("select_metadata_tab", ("patches",))
        for span in header.spans
    )


def test_dependency_header_does_not_render_legacy_shortcut_hint() -> None:
    view = VersionDetailsView()
    view._pane_selected = False

    view._active_section = 0
    inactive_header = view._render_dependency_header()

    view._pane_selected = True
    view._active_section = 1
    active_header = view._render_dependency_header()

    assert "[ / ]" not in inactive_header.plain
    assert "[ / ]" not in active_header.plain


def test_compare_table_renders_unchanged_rows_in_white_and_changed_rows_in_red_green() -> (
    None
):
    view = CompareDetailsView(
        VersionCompareData(
            left_selection=CompareSelection(
                "demo",
                VersionEntry(
                    version=Version("1.0.0"),
                    build="py313h123_0",
                    build_number=0,
                    subdir="noarch",
                    file_name="demo-1.0.0-py313h123_0.conda",
                ),
            ),
            right_selection=CompareSelection(
                "demo",
                VersionEntry(
                    version=Version("1.0.1"),
                    build="py313h123_0",
                    build_number=0,
                    subdir="noarch",
                    file_name="demo-1.0.1-py313h123_0.conda",
                ),
            ),
            metadata_rows=(
                CompareRow(
                    label="Name",
                    left="demo",
                    right="demo",
                    changed=False,
                ),
                CompareRow(
                    label="Version",
                    left="1.0.0",
                    right="1.0.1",
                    changed=True,
                ),
            ),
            dependencies=(),
            constraints=(),
            run_exports=(),
            files=(),
        )
    )

    table = cast(Table, view._render_metadata_body())

    assert table.columns[0].header == "Field"
    assert table.columns[1].header == "Left"
    assert table.columns[2].header == "Right"
    assert table.rows[0].style is None
    assert cast(Text, table.columns[1]._cells[0]).style == "white"
    assert cast(Text, table.columns[2]._cells[0]).style == "white"
    assert cast(Text, table.columns[1]._cells[1]).style == "red"
    assert cast(Text, table.columns[2]._cells[1]).style == "green"


def test_compare_dependency_table_uses_two_columns_with_blank_missing_values() -> None:
    view = CompareDetailsView(
        VersionCompareData(
            left_selection=CompareSelection(
                "demo",
                VersionEntry(
                    version=Version("1.0.0"),
                    build="py313h123_0",
                    build_number=0,
                    subdir="noarch",
                    file_name="demo-1.0.0-py313h123_0.conda",
                ),
            ),
            right_selection=CompareSelection(
                "demo",
                VersionEntry(
                    version=Version("1.0.1"),
                    build="py313h123_0",
                    build_number=0,
                    subdir="noarch",
                    file_name="demo-1.0.1-py313h123_0.conda",
                ),
            ),
            metadata_rows=(),
            dependencies=(
                CompareRow(
                    label="numpy",
                    left="numpy >=1.0",
                    right="",
                    changed=True,
                ),
                CompareRow(
                    label="pydantic",
                    left="",
                    right="pydantic >=2",
                    changed=True,
                ),
            ),
            constraints=(),
            run_exports=(),
            files=(),
        )
    )

    table = cast(Table, view._render_dependency_body("dependencies"))

    assert len(table.columns) == 2
    assert table.columns[0].header == "Left"
    assert table.columns[1].header == "Right"
    assert cast(Text, table.columns[0]._cells[0]).plain == "numpy >=1.0"
    assert cast(Text, table.columns[1]._cells[0]).plain == ""
    assert cast(Text, table.columns[0]._cells[1]).plain == ""
    assert cast(Text, table.columns[1]._cells[1]).plain == "pydantic >=2"


def test_compare_file_section_renders_option_list_rows_with_status_colors() -> None:
    class _HostApp(App[None]):
        pass

    async def _run() -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            screen = CompareScreen(
                VersionCompareData(
                    left_selection=CompareSelection(
                        "demo",
                        VersionEntry(
                            version=Version("1.0.0"),
                            build="py313h123_0",
                            build_number=0,
                            subdir="noarch",
                            file_name="demo-1.0.0-py313h123_0.conda",
                        ),
                    ),
                    right_selection=CompareSelection(
                        "demo",
                        VersionEntry(
                            version=Version("1.0.1"),
                            build="py313h123_0",
                            build_number=0,
                            subdir="noarch",
                            file_name="demo-1.0.1-py313h123_0.conda",
                        ),
                    ),
                    metadata_rows=(),
                    dependencies=(),
                    constraints=(),
                    run_exports=(),
                    files=(
                        CompareFileRow(
                            label="same.txt",
                            left="same.txt",
                            right="same.txt",
                            changed=False,
                        ),
                        CompareFileRow(
                            label="changed.txt",
                            left="changed.txt",
                            right="changed.txt",
                            changed=True,
                        ),
                        CompareFileRow(
                            label="left-only.txt",
                            left="left-only.txt",
                            right="",
                            changed=True,
                        ),
                        CompareFileRow(
                            label="right-only.txt",
                            left="",
                            right="right-only.txt",
                            changed=True,
                        ),
                    ),
                )
            )
            app.push_screen(screen)
            await pilot.pause()

            option_list = screen.query_one("#compare-option-list-2", DetailOptionList)
            prompts = [
                cast(Text, option_list.get_option_at_index(index).prompt)
                for index in range(option_list.option_count)
            ]

            assert [prompt.plain for prompt in prompts] == [
                "= same.txt",
                "~ changed.txt",
                "- left-only.txt",
                "+ right-only.txt",
            ]
            assert [prompt.style for prompt in prompts] == [
                "#5c6370",
                "#7a5c00",
                "#8b1e1e",
                "#1f5f2b",
            ]

    asyncio.run(_run())


def test_compare_file_rows_show_sizes_instead_of_sha256_values() -> None:
    row = CompareFileRow(
        label="info/index.json",
        left="info/index.json",
        right="info/index.json",
        changed=True,
        left_file=PackageFile("info/index.json", 1024, bytes.fromhex("11" * 32)),
        right_file=PackageFile("info/index.json", 2048, bytes.fromhex("22" * 32)),
    )

    option = CompareDetailsView._compare_file_suffix(row)

    assert option == " [L: 1.0 KiB | R: 2.0 KiB]"
    assert "11111111" not in option
    assert "22222222" not in option


def test_compare_symlinks_show_target_and_have_no_actions() -> None:
    symlink = PackageFile(
        path="info/current",
        size_in_bytes=13,
        path_type="softlink",
        link_target="recipe/meta.yaml",
    )
    row = CompareFileRow(
        label="current",
        left="info/current",
        right="info/current",
        changed=False,
        left_file=symlink,
        right_file=symlink,
    )

    assert CompareDetailsView._compare_file_suffix(row) == " -> recipe/meta.yaml"
    assert CondaMetadataTui._compare_file_action_options(row) == ()

    mixed_row = CompareFileRow(
        label="current",
        left="info/current",
        right="info/current",
        changed=True,
        left_file=symlink,
        right_file=PackageFile("info/current", 42),
    )
    assert CondaMetadataTui._compare_file_action_options(mixed_row) == (
        FileActionOption(action="preview", label="Preview right", source="right"),
        FileActionOption(action="download", label="Download right", source="right"),
    )


def test_unknown_compare_info_row_styles_only_marker_yellow() -> None:
    row = CompareFileRow(
        label="index.json",
        left="info/index.json",
        right="info/index.json",
        changed=False,
        left_file=PackageFile("info/index.json", 1234),
        right_file=PackageFile("info/index.json", 1234),
        comparison_known=False,
    )

    prompt = CompareDetailsView._render_compare_file_option(row)

    assert prompt.plain == "? index.json (1.2 KiB)"
    assert [
        (prompt.plain[span.start : span.end], span.style) for span in prompt.spans
    ] == [
        ("? ", "#7a5c00"),
        ("index.json", "#5c6370"),
        (" (1.2 KiB)", Style(color="#5c6370", dim=True)),
    ]


def test_dependency_header_keeps_selected_tab_colored_when_pane_is_inactive() -> None:
    view = VersionDetailsView()
    view._active_section = 0
    view._dependency_tab_index = 1
    view._details = _make_artifact_data(
        dependencies=("dep",),
        constraints=("constraint",),
        run_exports=RunExportsJson(weak=["run export"]),
    )

    header = view._render_dependency_header()

    assert "Constraints (1)" in header.plain
    assert any(
        span.style == INACTIVE_SELECTED_TAB_STYLE
        and header.plain[span.start : span.end] == "Constraints (1)"
        for span in header.spans
        if isinstance(span.style, Style)
    )
    assert header.style == INACTIVE_SECTION_TITLE_STYLE


def test_dependency_header_uses_inactive_section_style_for_unselected_tabs() -> None:
    view = VersionDetailsView()
    view._active_section = 0
    view._dependency_tab_index = 1
    view._details = _make_artifact_data(
        dependencies=("dep",),
        constraints=("constraint",),
        run_exports=RunExportsJson(weak=["run export"]),
    )

    header = view._render_dependency_header()

    assert any(
        span.style == INACTIVE_TAB_STYLE
        and header.plain[span.start : span.end] == "Run exports (1)"
        for span in header.spans
        if isinstance(span.style, Style)
    )


def test_dependency_header_uses_active_title_style_when_pane_is_selected() -> None:
    view = VersionDetailsView()
    view._pane_selected = True
    view._active_section = 1
    view._details = _make_artifact_data(dependencies=("dep",))

    header = view._render_dependency_header()

    assert header.style == ACTIVE_SECTION_TITLE_STYLE


def test_file_header_shows_pkg_and_info_as_clickable_tabs() -> None:
    view = VersionDetailsView()
    view._details = _make_artifact_data(
        file_paths=(PackageFile("bin/demo"),),
        info_files=(PackageFile("info/index.json"), PackageFile("info/paths.json")),
    )
    view._file_tab_index = 1

    header = view._render_file_header()

    assert header.plain == "[3] pkg/ (1) - info/ (2)"
    assert any(
        span.style.meta == {"@click": ("select_file_tab", ("info",))}
        for span in header.spans
        if isinstance(span.style, Style)
    )
    assert any(
        span.style == INACTIVE_SELECTED_TAB_STYLE
        and header.plain[span.start : span.end] == "info/ (2)"
        for span in header.spans
        if isinstance(span.style, Style)
    )


def test_dependency_header_shows_zero_counts_when_sections_are_empty() -> None:
    view = VersionDetailsView()
    view._details = _make_artifact_data()

    header = view._render_dependency_header()

    assert "Dependencies (0)" in header.plain
    assert "Constraints (0)" in header.plain
    assert "Run exports (0)" in header.plain


def test_dependency_header_omits_counts_before_details_are_loaded() -> None:
    view = VersionDetailsView()

    header = view._render_dependency_header()

    assert "Dependencies" in header.plain
    assert "Constraints" in header.plain
    assert "Run exports" in header.plain
    assert "(0)" not in header.plain


def test_run_export_list_entry_uses_plain_matchspec() -> None:
    view = VersionDetailsView()
    view._details = _make_artifact_data(
        run_exports=RunExportsJson(weak=["python_abi 3.13.* *_cp313"])
    )

    entries = view._dependency_entries_for_tab("run_exports")

    assert entries[0].label == "weak: python_abi 3.13.* *_cp313"
    assert entries[0].matchspec == "python_abi 3.13.* *_cp313"


def test_dependency_list_entry_unescapes_matchspec_text() -> None:
    view = VersionDetailsView()
    view._details = _make_artifact_data(
        dependencies=(r"demo \[version='>=1'\]",),
    )

    entries = view._dependency_entries_for_tab("dependencies")

    assert entries[0].label == "demo [version='>=1']"
    assert entries[0].matchspec == "demo [version='>=1']"


def test_file_list_entry_uses_plain_file_path() -> None:
    view = VersionDetailsView()
    view._details = _make_artifact_data(
        file_paths=(
            PackageFile("site-packages/demo.py", 1536),
            PackageFile("bin/demo", path_type="softlink"),
        ),
    )

    entries = view._file_entries_for_details()

    assert entries[0].label == "site-packages/demo.py (1.5 KiB)"
    assert entries[0].path == "site-packages/demo.py"
    assert entries[1].label == "bin/demo"
    assert entries[1].path is None


def test_info_file_list_entries_use_archive_paths_and_sizes() -> None:
    view = VersionDetailsView()
    view._details = _make_artifact_data(
        info_files=(
            PackageFile("info/index.json", 1024),
            PackageFile("info/recipe/meta.yaml", 1536),
            PackageFile(
                path="info/current",
                size_in_bytes=13,
                path_type="softlink",
                link_target="recipe/meta.yaml",
            ),
        ),
    )

    entries = view._file_entries_for_details("info")

    assert [entry.label for entry in entries] == [
        "index.json (1.0 KiB)",
        "recipe/meta.yaml (1.5 KiB)",
        "current -> recipe/meta.yaml",
    ]
    assert [entry.path for entry in entries] == [
        "info/index.json",
        "info/recipe/meta.yaml",
        None,
    ]
    assert [entry.size_in_bytes for entry in entries] == [1024, 1536, 13]


def test_compare_screen_renders_footer_with_keybinds() -> None:
    class _HostApp(App[None]):
        pass

    async def _run() -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            screen = CompareScreen(
                VersionCompareData(
                    left_selection=CompareSelection(
                        "demo",
                        VersionEntry(
                            version=Version("1.0.0"),
                            build="py313h123_0",
                            build_number=0,
                            subdir="noarch",
                            file_name="demo-1.0.0-py313h123_0.conda",
                        ),
                    ),
                    right_selection=CompareSelection(
                        "demo",
                        VersionEntry(
                            version=Version("1.0.1"),
                            build="py313h456_0",
                            build_number=0,
                            subdir="linux-64",
                            file_name="demo-1.0.1-py313h456_0.conda",
                        ),
                    ),
                    metadata_rows=(),
                    dependencies=(),
                    constraints=(),
                    run_exports=(),
                    files=(),
                )
            )
            app.push_screen(screen)
            await pilot.pause()

            assert (
                str(screen.query_one("#compare-footer", Static).render())
                == "Tab/Shift+Tab panes | Enter: file actions | Swap: x | Back: esc | Quit: q | Help: ?"
            )

    asyncio.run(_run())


def test_clicking_detail_section_activates_and_focuses_pane() -> None:
    activated: list[int] = []
    section = DetailSection("Files", 2, on_activate=activated.append)

    event = _FakeClickEvent()
    section.on_click(event)  # type: ignore[arg-type]

    assert activated == [2]
    assert event.stopped is True


def test_clicking_dependency_tab_dispatches_without_hover_link_action() -> None:
    selected_tabs: list[str] = []
    section = DetailSection(
        "Dependencies",
        1,
        on_activate=lambda _index: None,
        on_select_dependency_tab=selected_tabs.append,
    )

    section.action_select_dependency_tab("constraints")
    assert selected_tabs == ["constraints"]


def test_clicking_dependency_tab_does_not_activate_section_click_handler() -> None:
    activated: list[int] = []
    section = DetailSection("Dependencies", 1, on_activate=activated.append)

    event = _FakeClickEvent(
        Style(
            meta={
                "@click": (
                    "select_dependency_tab",
                    ("constraints",),
                )
            }
        )
    )
    section.on_click(event)  # type: ignore[arg-type]

    assert activated == []
    assert event.stopped is True


def test_whoneeds_gateway_tracks_and_releases_the_scanned_channel() -> None:
    app = CondaMetadataTui()
    gateway = _RecordingGateway()
    app._whoneeds_gateway = cast(Gateway, gateway)
    app._platforms = [Platform("noarch")]

    asyncio.run(app._query_whoneeds_records("python"))

    app._channel_name = "robostack"
    app._release_whoneeds_repodata()

    assert gateway.queries == [
        (["conda-forge"], [Platform("noarch")], "python"),
    ]
    assert gateway.cleared == ["conda-forge"]
    assert app._whoneeds_scanned_channel is None

    # Nothing is cached anymore, so releasing again must not touch the gateway.
    app._release_whoneeds_repodata()

    assert gateway.cleared == ["conda-forge"]


def test_footer_text_matches_redesigned_shortcuts() -> None:
    app = CondaMetadataTui()

    assert (
        app._footer_text()
        == "Search: / | Platform: p | Channel: c | MatchSpec: m | Who needs: w | Help: ?"
    )


def test_footer_text_shows_download_hint_in_versions_mode() -> None:
    app = CondaMetadataTui()
    app._mode = "versions"

    assert (
        cast(Text, app._footer_text()).plain
        == "Search: / | Platform: p | Channel: c | MatchSpec: m | Who needs: w | Compare: C | Download: d | Help: ?"
    )


def test_footer_text_highlights_compare_hint_when_compare_a_is_stored() -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    app._compare_selection = CompareSelection(
        "demo",
        VersionEntry(
            version=Version("1.2.3"),
            build="py313h123_0",
            build_number=0,
            subdir="noarch",
            file_name="demo-1.2.3-py313h123_0.conda",
        ),
    )

    footer = cast(Text, app._footer_text())

    assert footer.plain == (
        "Search: / | Platform: p | Channel: c | MatchSpec: m | Who needs: w | Compare: C | Download: d | Help: ?"
    )
    compare_start = footer.plain.index("Compare: C")
    compare_end = compare_start + len("Compare: C")
    assert any(
        span.start == compare_start
        and span.end == compare_end
        and span.style == "#ec4899"
        for span in footer.spans
        if isinstance(span.style, str)
    )
    assert not any(
        footer.plain[span.start : span.end] == "Download: d"
        for span in footer.spans
        if isinstance(span.style, str)
    )


def test_footer_text_shows_compare_keybinds_when_compare_screen_is_open() -> None:
    app = CondaMetadataTui()
    app._compare_screen_open = True

    assert (
        app._footer_text()
        == "Compare: Tab/Shift+Tab panes | Swap: x | Back: esc | Quit: q | Help: ?"
    )


def test_footer_text_shows_live_search_query_in_filter_mode() -> None:
    app = CondaMetadataTui()
    app._filter_mode = True
    app._search_query = "polars"

    assert app._footer_text() == "Search: polars_"


def test_footer_text_shows_live_channel_draft_in_channel_edit_mode() -> None:
    app = CondaMetadataTui()
    app._channel_edit_mode = True
    app._channel_draft = "prefix.dev/conda-forge"

    assert app._footer_text() == "Channel: prefix.dev/conda-forge_"


def test_footer_text_resets_in_versions_mode_even_with_active_search() -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    app._filter_mode = True
    app._search_query = "polars"

    assert (
        cast(Text, app._footer_text()).plain
        == "Search: / | Platform: p | Channel: c | MatchSpec: m | Who needs: w | Compare: C | Download: d | Help: ?"
    )


def test_file_action_metadata_lines_formats_sha256() -> None:
    rendered = CondaMetadataTui._file_action_metadata_lines(
        sha256=bytes.fromhex("ab" * 32)
    )

    assert rendered == (
        "SHA256: abababababababababababababababababababababababababababababababab",
    )


def test_preview_content_rejects_binary_files() -> None:
    rendered = CondaMetadataTui._preview_content("lib/demo.so", b"\0binary")

    assert "Binary file preview is not supported." in rendered.text
    assert rendered.lexer is None


def test_preview_content_rejects_invalid_utf8_without_null_bytes() -> None:
    rendered = CondaMetadataTui._preview_content(
        "info/about.json", b"\xff\xfe\x80invalid"
    )

    assert "Binary file preview is not supported." in rendered.text
    assert rendered.lexer is None


def test_preview_content_rejects_large_files() -> None:
    rendered = CondaMetadataTui._preview_content("info/about.json", b"x" * 300_000)

    assert "File too large to preview in-app" in rendered.text
    assert "293.0 KiB" in rendered.text
    assert rendered.lexer is None


@pytest.mark.parametrize(
    ("file_path", "expected"),
    [
        ("site-packages/demo.py", "python"),
        ("info/about.JSON", "json"),
        ("info/recipe/meta.yaml", "yaml"),
        ("info/recipe/conda_build_config.yml", "yaml"),
        ("src/demo.cpp", "cpp"),
        ("src/lib.rs", "rust"),
        ("share/docs/README.md", "markdown"),
        ("recipe/run_test.bat", "batch"),
        ("recipe/Dockerfile", "docker"),
        ("src/CMakeLists.txt", "cmake"),
        ("src/Makefile", "make"),
    ],
)
def test_syntax_lexer_for_path_supports_common_package_files(
    file_path: str, expected: str
) -> None:
    assert syntax_lexer_for_path(file_path) == expected


def test_syntax_lexer_for_path_returns_none_for_unknown_file() -> None:
    assert syntax_lexer_for_path("share/demo/LICENSE") is None


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"x" * 10_800_000, "lib/libstdc++.so (10.3 MiB)"),
        (b"x" * 600, "lib/libstdc++.so (600 B)"),
    ],
    ids=["mebibytes", "bytes"],
)
def test_preview_title_uses_human_readable_size(content: bytes, expected: str) -> None:
    assert CondaMetadataTui._preview_title("lib/libstdc++.so", content) == expected


def test_file_preview_screen_uses_plain_static_text() -> None:
    class _HostApp(App[None]):
        pass

    async def _run() -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            screen = FilePreviewScreen("info/[about].json", "[demo]\n")
            app.push_screen(screen)
            await pilot.pause()

            assert screen.query_one("#file-preview-title")._render_markup is False
            assert screen.query_one("#file-preview-body")._render_markup is False

    asyncio.run(_run())


def test_file_preview_screen_uses_syntax_renderable() -> None:
    class _HostApp(App[None]):
        pass

    async def _run() -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            screen = FilePreviewScreen(
                "demo.py", "print('demo')\n", syntax_lexer="python"
            )
            app.push_screen(screen)
            await pilot.pause()

            body = screen.query_one("#file-preview-body", Static)
            assert isinstance(body.content, Syntax)
            assert body.content.code == "print('demo')\n"

    asyncio.run(_run())


def test_file_action_screen_uses_plain_static_text() -> None:
    class _HostApp(App[None]):
        pass

    async def _run() -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            screen = FileActionScreen(
                "info/[about].json",
                actions=(
                    FileActionOption(action="preview", label="Preview"),
                    FileActionOption(action="download", label="Download"),
                ),
                metadata_lines=(
                    "SHA256: abababababababababababababababababababababababababababababababab",
                ),
            )
            app.push_screen(screen)
            await pilot.pause()

            assert screen.query_one("#file-action-path")._render_markup is False
            assert screen.query_one("#file-action-metadata")._render_markup is False

    asyncio.run(_run())


def test_download_path_screen_uses_plain_static_text() -> None:
    class _HostApp(App[None]):
        pass

    async def _run() -> None:
        app = _HostApp()
        async with app.run_test() as pilot:
            screen = DownloadPathScreen(
                "info/[about].json",
                default_destination="info/[about].json",
            )
            app.push_screen(screen)
            await pilot.pause()

            assert screen.query_one("#download-path-file")._render_markup is False

    asyncio.run(_run())
