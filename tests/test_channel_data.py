"""Real-data tests against the offline fixture channel (no TUI involved).

These replace hand-built ``RepoDataRecord`` objects and monkeypatched loaders
with records and archives produced by py-rattler from real conda-forge
packages, served by the local channel from ``conftest.py``.
"""

from __future__ import annotations

import asyncio

import pytest
from rattler.match_spec import MatchSpec
from rattler.networking import Client
from rattler.platform import Platform
from syrupy.assertion import SnapshotAssertion

from pixi_browse.models import VersionArtifactData
from pixi_browse.rendering import (
    format_version_details_metadata_lines,
    format_version_details_run_exports,
    render_package_preview,
)
from pixi_browse.repodata import (
    discover_available_platforms,
    fetch_package_names,
    merge_available_platforms,
    normalize_channel_names,
    query_matchspec_records,
    query_package_records,
    query_whoneeds_records,
)
from pixi_browse.tui import CondaMetadataTui
from pixi_browse.tui.version_loader import VersionDataLoader
from tests.helpers import (
    BIOCONDA_CHANNEL,
    CHANNEL_PLATFORMS,
    MAIN_CHANNEL,
    MISSING_CHANNEL,
    GatewayFactory,
)


def test_discover_available_platforms_finds_indexed_subdirs(
    make_gateway: GatewayFactory,
) -> None:
    platforms_by_channel = asyncio.run(
        discover_available_platforms(
            gateway=make_gateway(), channel_names=[MAIN_CHANNEL]
        )
    )

    assert platforms_by_channel == {
        MAIN_CHANNEL: [
            Platform("linux-64"),
            Platform("osx-arm64"),
            Platform("noarch"),
        ]
    }


def test_discover_available_platforms_probes_every_channel_separately(
    make_gateway: GatewayFactory,
) -> None:
    """``bioconda`` only ships noarch packages and ``missing`` has no repodata
    at all; both stay visible next to the platforms of ``conda-forge``."""
    platforms_by_channel = asyncio.run(
        discover_available_platforms(
            gateway=make_gateway(),
            channel_names=[MAIN_CHANNEL, BIOCONDA_CHANNEL, MISSING_CHANNEL],
        )
    )

    assert platforms_by_channel == {
        MAIN_CHANNEL: [
            Platform("linux-64"),
            Platform("osx-arm64"),
            Platform("noarch"),
        ],
        BIOCONDA_CHANNEL: [Platform("noarch")],
        MISSING_CHANNEL: [],
    }
    assert merge_available_platforms(platforms_by_channel) == [
        Platform("linux-64"),
        Platform("osx-arm64"),
        Platform("noarch"),
    ]


def test_normalize_channel_names_strips_dedupes_and_defaults() -> None:
    assert normalize_channel_names([" bioconda ", "conda-forge", "bioconda", ""]) == [
        "bioconda",
        "conda-forge",
    ]
    assert normalize_channel_names([]) == ["conda-forge"]
    assert normalize_channel_names(["", "  "]) == ["conda-forge"]


def test_fetch_package_names_lists_channel_packages(
    make_gateway: GatewayFactory,
) -> None:
    platforms, names = asyncio.run(
        fetch_package_names(
            gateway=make_gateway(),
            channel_names=[MAIN_CHANNEL],
            selected_platforms=CHANNEL_PLATFORMS,
        )
    )

    assert platforms == list(CHANNEL_PLATFORMS)
    assert names == ["libzlib", "pixi-browse", "polars", "six", "zlib"]


def test_fetch_package_names_merges_all_channels(
    make_gateway: GatewayFactory,
) -> None:
    _platforms, names = asyncio.run(
        fetch_package_names(
            gateway=make_gateway(),
            channel_names=[MAIN_CHANNEL, BIOCONDA_CHANNEL],
            selected_platforms=CHANNEL_PLATFORMS,
        )
    )

    assert names == [
        "libzlib",
        "pixi-browse",
        "polars",
        "pyfaidx",
        "six",
        "snakemake-wrapper-utils",
        "zlib",
    ]


def test_query_whoneeds_records_spans_all_channels(
    make_gateway: GatewayFactory,
) -> None:
    """``pyfaidx`` (bioconda) depends on ``six`` (conda-forge); the reverse
    query sees the dependency across the channel boundary."""
    result = asyncio.run(
        query_whoneeds_records(
            gateway=make_gateway(sharded_enabled=False),
            channel_names=[MAIN_CHANNEL, BIOCONDA_CHANNEL],
            platforms=list(CHANNEL_PLATFORMS),
            target="six",
            log=lambda _message: None,
        )
    )

    assert result.package_names == ["pyfaidx"]
    assert all(
        str(record.url).startswith("https://conda.anaconda.org/bioconda/")
        for record in result.records_by_package["pyfaidx"]
    )


def test_query_package_records_sorts_newest_first(
    make_gateway: GatewayFactory,
) -> None:
    records = asyncio.run(
        query_package_records(
            gateway=make_gateway(),
            channel_names=["conda-forge"],
            platforms=list(CHANNEL_PLATFORMS),
            package_name="libzlib",
        )
    )

    assert [str(record.version) for record in records] == [
        "1.3.2",
        "1.3.2",
        "1.3.1",
        "1.3.1",
    ]
    assert {(record.subdir, record.build) for record in records} == {
        ("linux-64", "h25fd6f3_3"),
        ("osx-arm64", "h8088a28_3"),
        ("linux-64", "hb9d3cd8_2"),
        ("osx-arm64", "h8359307_2"),
    }
    assert all(
        str(record.url).startswith("https://conda.anaconda.org/conda-forge/")
        for record in records
    )


def test_query_matchspec_records_groups_matching_records(
    make_gateway: GatewayFactory,
) -> None:
    result = asyncio.run(
        query_matchspec_records(
            gateway=make_gateway(),
            channel_names=["conda-forge"],
            platforms=list(CHANNEL_PLATFORMS),
            matchspec=MatchSpec("libzlib >=1.3.2", exact_names_only=False),
        )
    )

    assert result.package_names == ["libzlib"]
    assert {str(record.version) for record in result.records_by_package["libzlib"]} == {
        "1.3.2"
    }


def test_query_whoneeds_records_finds_zlib_depending_on_libzlib(
    make_gateway: GatewayFactory,
) -> None:
    logs: list[str] = []
    result = asyncio.run(
        query_whoneeds_records(
            gateway=make_gateway(sharded_enabled=False),
            channel_names=["conda-forge"],
            platforms=list(CHANNEL_PLATFORMS),
            target="libzlib",
            log=logs.append,
        )
    )

    # rattler also reports records whose *run exports* reference the target,
    # which is why libzlib shows up as needing itself.
    assert result.package_names == ["libzlib", "zlib"]
    assert {record.subdir for record in result.records_by_package["zlib"]} == {
        "linux-64",
        "osx-arm64",
    }
    assert {str(record.version) for record in result.records_by_package["libzlib"]} == {
        "1.3.1",
        "1.3.2",
    }
    assert logs[0].startswith("who-needs: starting gateway reverse query")


def test_render_package_preview_from_real_records(
    make_gateway: GatewayFactory, snapshot: SnapshotAssertion
) -> None:
    records = asyncio.run(
        query_package_records(
            gateway=make_gateway(),
            channel_names=["conda-forge"],
            platforms=list(CHANNEL_PLATFORMS),
            package_name="libzlib",
        )
    )

    assert render_package_preview("libzlib", records) == snapshot


@pytest.mark.parametrize(
    ("package_name", "subdir", "version"),
    [
        ("pixi-browse", "noarch", "0.0.14"),
        ("libzlib", "linux-64", "1.3.2"),
        ("six", "noarch", "1.16.0"),
    ],
)
def test_load_version_artifact_data_reads_real_archives(
    make_gateway: GatewayFactory,
    rattler_client: Client,
    snapshot: SnapshotAssertion,
    package_name: str,
    subdir: str,
    version: str,
) -> None:
    """Metadata, run exports and file lists come straight out of the archive,
    for both ``.conda`` and legacy ``.tar.bz2`` packages."""

    async def load() -> tuple[list[str], list[str], list[str], list[str], list[str]]:
        records = await query_package_records(
            gateway=make_gateway(),
            channel_names=["conda-forge"],
            platforms=[Platform(subdir)],
            package_name=package_name,
        )
        record = next(record for record in records if str(record.version) == version)
        loader = VersionDataLoader(client=rattler_client)
        details = await loader.load_version_artifact_data(
            package_name,
            record,
            preview_key=(
                package_name,
                version,
                record.build,
                record.build_number,
                record.subdir,
                record.file_name,
            ),
        )
        files = [
            f"{file.path} -> {file.link_target}" if file.is_symlink else file.path
            for file in details.file_paths
        ]
        return (
            list(format_version_details_metadata_lines(details)),
            list(details.dependencies),
            list(format_version_details_run_exports(details.run_exports)),
            files + [file.path for file in details.info_files],
            [
                f"{row.label}: {row.left!r} -> {row.right!r}"
                for row in details.repodata_patches.rows
            ],
        )

    metadata, dependencies, run_exports, files, repodata_patches = asyncio.run(load())

    assert {
        "metadata": metadata,
        "dependencies": dependencies,
        "run_exports": run_exports,
        "files": files,
        # The fixture repodata is generated from the packages' own index.json,
        # so anything reported here is a false positive of the patch detection.
        "repodata_patches": repodata_patches,
    } == snapshot


def test_load_version_artifact_data_is_cached_per_preview_key(
    make_gateway: GatewayFactory, rattler_client: Client
) -> None:
    """The archive, its paths and the assembled details are read once per
    artifact; later requests for the same key reuse the cached objects."""

    async def load_twice() -> tuple[VersionArtifactData, VersionArtifactData]:
        records = await query_package_records(
            gateway=make_gateway(),
            channel_names=["conda-forge"],
            platforms=[Platform("noarch")],
            package_name="six",
        )
        record = records[0]
        loader = VersionDataLoader(client=rattler_client)
        preview_key = (
            "six",
            str(record.version),
            record.build,
            record.build_number,
            record.subdir,
            record.file_name,
        )
        first = await loader.load_version_details(
            "six", record, preview_key=preview_key
        )
        archive = await loader.get_package_archive(preview_key, str(record.url))
        second = await loader.load_version_artifact_data(
            "six", record, preview_key=preview_key
        )
        assert loader.archive_cache[preview_key] is archive
        assert preview_key in loader.paths_cache
        assert preview_key in loader.about_urls_cache
        return first, second

    first, second = asyncio.run(load_twice())

    assert second is first
    assert first.dependencies == ("python >=3.9",)


def test_app_shares_the_client_with_its_version_loader(rattler_client: Client) -> None:
    app = CondaMetadataTui(client=rattler_client)

    assert app._client is rattler_client
    assert app._version_loader._client is rattler_client


def test_app_creates_a_default_client_when_none_is_given() -> None:
    app = CondaMetadataTui()

    assert isinstance(app._client, Client)
    assert app._version_loader._client is app._client
