import asyncio
from datetime import UTC, datetime
from typing import cast

from rattler.networking import Client
from rattler.platform import Platform
from rattler.repo_data import Gateway, PackageFormatSelection, SourceConfig

import pixi_browse.repodata as repodata_module
from pixi_browse.repodata import (
    RepodataRevisions,
    create_gateway,
    fetch_repodata_revisions,
    format_repodata_revisions_summary,
    repodata_revision_number,
)

LINUX = Platform("linux-64")
NOARCH = Platform("noarch")
OSX = Platform("osx-arm64")


def test_create_gateway_selects_conda_archives_only(monkeypatch) -> None:
    created: list[tuple[SourceConfig, object, bool]] = []

    class _FakeGateway:
        def __init__(
            self,
            *,
            default_config: SourceConfig,
            client: Client | None,
            show_progress: bool,
        ) -> None:
            created.append((default_config, client, show_progress))

    monkeypatch.setattr(repodata_module, "Gateway", _FakeGateway)
    client = object()

    create_gateway(client=cast(Client, client), sharded_enabled=False)

    assert len(created) == 1
    default_config, passed_client, show_progress = created[0]
    assert passed_client is client
    assert show_progress is False
    assert default_config.sharded_enabled is False
    assert default_config.cache_action == "cache-or-fetch"
    # Sharded and full repodata must agree, and `.whl` records cannot be
    # inspected as conda archives, so the selection is always explicit.
    assert (
        default_config.package_format_selection is PackageFormatSelection.PREFER_CONDA
    )


def test_fetch_repodata_revisions_queries_each_platform_in_display_order() -> None:
    calls: list[tuple[str, Platform]] = []
    v3: RepodataRevisions = {"v3": {"n_packages": 2}}

    class _FakeGateway:
        async def repodata_revisions(
            self, channel: str, platform: Platform
        ) -> RepodataRevisions:
            calls.append((channel, platform))
            return v3 if platform == NOARCH else {}

    result = asyncio.run(
        fetch_repodata_revisions(
            gateway=cast(Gateway, _FakeGateway()),
            channel_name="conda-forge",
            platforms=[OSX, NOARCH, LINUX, NOARCH],
        )
    )

    # Platforms are queried once each, alphabetically with noarch last.
    assert calls == [
        ("conda-forge", LINUX),
        ("conda-forge", OSX),
        ("conda-forge", NOARCH),
    ]
    assert list(result) == [LINUX, OSX, NOARCH]
    assert result[NOARCH] == v3
    assert result[LINUX] == {}


def test_repodata_revision_number() -> None:
    assert repodata_revision_number("v3") == 3
    assert repodata_revision_number("V12") == 12
    assert repodata_revision_number("legacy") is None
    assert repodata_revision_number("vX") is None


def test_format_repodata_revisions_summary_is_empty_for_legacy_channels() -> None:
    assert format_repodata_revisions_summary({}) is None
    assert format_repodata_revisions_summary({LINUX: {}, NOARCH: {}}) is None


def test_format_repodata_revisions_summary_sums_packages_across_platforms() -> None:
    summary = format_repodata_revisions_summary(
        {
            LINUX: {"v3": {"n_packages": 40, "message": "wheels ahead"}},
            NOARCH: {
                "v3": {
                    "n_packages": 2,
                    "oldest": datetime(2026, 1, 1, tzinfo=UTC),
                }
            },
            OSX: {},
        }
    )

    assert summary == "Repodata revisions: v3 (42 packages)"


def test_format_repodata_revisions_summary_marks_unknown_revisions() -> None:
    summary = format_repodata_revisions_summary(
        {
            LINUX: {"v5": {"n_packages": 1}, "v3": {"n_packages": 3}},
            NOARCH: {"v3": {}, "beta": {"n_packages": 7}},
        }
    )

    # Counts are only shown when every platform reports one, revisions sort
    # numerically, and anything rattler cannot read is flagged.
    assert summary == (
        "Repodata revisions: v3, v5 (1 package) [unsupported], "
        "beta (7 packages) [unsupported]"
    )
