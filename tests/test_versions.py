import asyncio
import shutil
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from rattler.exceptions import InvalidMatchSpecError
from rattler.match_spec import MatchSpec
from rattler.package import NoArchLiteral, RunExportsJson
from rattler.platform import Platform
from rattler.repo_data import PackageRecord, RepoDataRecord
from rattler.version import Version
from rich.style import Style
from rich.text import Text
from textual.events import Paste

from pixi_browse import __version__
from pixi_browse.__main__ import CondaMetadataTui, VersionEntry, VersionRow
from pixi_browse.models import VersionDetailsData
from pixi_browse.rendering import (
    build_version_details_data,
    format_clickable_github_handle,
    format_clickable_github_handle_list,
    format_clickable_url,
    format_clickable_url_list,
    format_provenance,
    render_package_preview,
    render_selected_version_details,
)
from pixi_browse.repodata import MatchSpecQueryResult
from pixi_browse.tui import (
    ACTIVE_SECTION_TITLE_STYLE,
    EMPTY_MATCHSPEC_RESULT,
    INACTIVE_SECTION_TITLE_STYLE,
    INACTIVE_SELECTED_TAB_STYLE,
    INACTIVE_TAB_STYLE,
    DetailSection,
    Empty,
    HelpScreen,
    MainPanel,
    MatchSpecScreen,
    SidebarPanel,
    VersionDetailsView,
)
from pixi_browse.tui.state import AboutUrls


@dataclass(frozen=True)
class _Record:
    version: Version
    build: str
    build_number: int
    subdir: str
    file_name: str


@dataclass(frozen=True)
class _RecordWithUrl:
    url: str


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


class _FakeKeyEvent:
    def __init__(self, key: str, character: str | None = None) -> None:
        self.key = key
        self.character = character
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeClickEvent:
    def __init__(self, style: Style | None = None) -> None:
        self.stopped = False
        self.style = style

    def stop(self) -> None:
        self.stopped = True


def test_conda_metadata_tui_uses_one_shared_authenticated_client(monkeypatch) -> None:
    shared_client = object()
    gateway_calls: list[object] = []

    def _fake_create_gateway(*, client: object | None = None) -> object:
        gateway_calls.append(client)
        return object()

    monkeypatch.setattr(
        "pixi_browse.tui.Client.default_client",
        lambda: shared_client,
    )
    monkeypatch.setattr(
        "pixi_browse.tui.create_gateway",
        _fake_create_gateway,
    )

    app = CondaMetadataTui()

    assert app._client is shared_client
    assert gateway_calls == [shared_client]


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


def test_render_selected_version_details_includes_package_paths() -> None:
    record = _make_repo_data_record(
        version="1.2.3",
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
        depends=["python >=3.13"],
    )

    rendered = render_selected_version_details(
        "demo",
        record,
        content_width=90,
        package_paths=["bin/demo", "lib/python3.13/site-packages/demo.py"],
    )

    assert "Files:" in rendered
    assert " - bin/demo" in rendered
    assert " - lib/python3.13/site-packages/demo.py" in rendered
    assert "placeholder: coming soon" not in rendered


def test_build_version_details_data_aligns_metadata_rows() -> None:
    record = _make_repo_data_record(
        version="1.2.3",
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )

    details = build_version_details_data(
        "demo",
        record,
        repository_urls=["https://github.com/example/demo"],
        documentation_urls=["https://docs.example.com/demo"],
    )

    assert "Package               demo" in details.metadata_lines
    assert "Python Site-Packages  not available" in details.metadata_lines
    assert any(
        line.startswith("Repository            [@click=app.open_external_url(")
        for line in details.metadata_lines
    )
    assert (
        "Built with            rattler-build 0.47.0"
        in build_version_details_data(
            "demo",
            record,
            rattler_build_version="0.47.0",
        ).metadata_lines
    )


def test_build_version_details_data_formats_run_exports_from_py_rattler() -> None:
    record = _make_repo_data_record(
        version="1.2.3",
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )

    details = build_version_details_data(
        "demo",
        record,
        run_exports=RunExportsJson(
            weak=["python_abi 3.13.* *_cp313"],
            strong=["libdemo >=1.2.3"],
            noarch=["python"],
        ),
    )

    assert details.run_exports == (
        "weak: python_abi 3.13.* *_cp313",
        "strong: libdemo >=1.2.3",
        "noarch: python",
    )
    assert details.dependencies == ()
    assert details.constraints == ()
    assert len(details.run_exports) == 3


def test_render_selected_version_details_includes_about_urls() -> None:
    record = _make_repo_data_record(
        version="1.2.3",
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )

    rendered = render_selected_version_details(
        "demo",
        record,
        content_width=90,
        repository_urls=["https://github.com/example/demo"],
        documentation_urls=["https://docs.example.com/demo"],
        homepage_urls=["https://example.com/demo"],
        recipe_maintainers=["@pavelzw", "xhochy"],
        provenance_remote_url="https://github.com/conda-forge/polars-feedstock.git",
        provenance_sha="f48623bd7b6d92b6573f21a907a62c8e06b75c5c",
        rattler_build_version="0.38.0",
    )

    assert (
        "URL: [@click=app.open_external_url('https://example.invalid/demo-1.2.3-py313h123_0.conda')]"
        in rendered
    )
    assert (
        "Repository: [@click=app.open_external_url('https://github.com/example/demo')]"
        in rendered
    )
    assert (
        "Documentation: [@click=app.open_external_url('https://docs.example.com/demo')]"
        in rendered
    )
    assert (
        "Homepage: [@click=app.open_external_url('https://example.com/demo')]"
        in rendered
    )
    assert (
        "Recipe maintainers: "
        "[@click=app.open_external_url('https://github.com/pavelzw')]@pavelzw[/], "
        "[@click=app.open_external_url('https://github.com/xhochy')]@xhochy[/]"
        in rendered
    )
    assert (
        "Provenance: "
        "[@click=app.open_external_url('https://github.com/conda-forge/polars-feedstock/commit/f48623bd7b6d92b6573f21a907a62c8e06b75c5c')]"
        "conda-forge/polars-feedstock@f48623bd7b6d92b6573f21a907a62c8e06b75c5c[/]"
        in rendered
    )
    assert "Built with rattler-build 0.38.0" in rendered
    assert "https://github.com/example/demo" in rendered
    assert "https://docs.example.com/demo" in rendered
    assert "https://example.com/demo" in rendered
    assert "@click=app.open_external_url(" in rendered


def test_format_clickable_url_uses_textual_click_action() -> None:
    rendered = format_clickable_url("https://example.com/demo")

    assert (
        rendered
        == "[@click=app.open_external_url('https://example.com/demo')]https://example.com/demo[/]"
    )


def test_format_clickable_url_list_compacts_urls_to_single_line() -> None:
    rendered = format_clickable_url_list(
        "Repository:",
        [
            "https://example.com/one",
            "https://example.com/two",
        ],
    )

    assert rendered == [
        "Repository: "
        "[@click=app.open_external_url('https://example.com/one')]https://example.com/one[/], "
        "[@click=app.open_external_url('https://example.com/two')]https://example.com/two[/]"
    ]


def test_format_clickable_github_handle_uses_github_profile() -> None:
    rendered = format_clickable_github_handle("@pavelzw")

    assert (
        rendered
        == "[@click=app.open_external_url('https://github.com/pavelzw')]@pavelzw[/]"
    )


def test_format_clickable_github_handle_list_compacts_handles_to_single_line() -> None:
    rendered = format_clickable_github_handle_list(
        "Recipe maintainers:",
        ["@pavelzw", "xhochy"],
    )

    assert rendered == [
        "Recipe maintainers: "
        "[@click=app.open_external_url('https://github.com/pavelzw')]@pavelzw[/], "
        "[@click=app.open_external_url('https://github.com/xhochy')]@xhochy[/]"
    ]


def test_format_provenance_uses_github_commit_link() -> None:
    rendered = format_provenance(
        "https://github.com/conda-forge/polars-feedstock.git",
        "f48623bd7b6d92b6573f21a907a62c8e06b75c5c",
    )

    assert rendered == [
        "Provenance: "
        "[@click=app.open_external_url('https://github.com/conda-forge/polars-feedstock/commit/f48623bd7b6d92b6573f21a907a62c8e06b75c5c')]"
        "conda-forge/polars-feedstock@f48623bd7b6d92b6573f21a907a62c8e06b75c5c[/]"
    ]


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

    rendered = render_package_preview(
        "demo",
        records,
        record_sort_key=lambda record: (
            record.version,
            record.build,
            record.subdir,
            record.build_number,
        ),
    )

    assert "Version selector preview" in rendered
    assert "Press Enter to open the version list." in rendered
    assert "▾ linux-64 (1)" in rendered
    assert "▾ noarch (1)" in rendered
    assert "1.2.3" in rendered
    assert "py313h123_1" in rendered
    assert "URL" not in rendered
    assert "Dependencies" not in rendered


def test_get_package_paths_caches_remote_paths(monkeypatch) -> None:
    app = CondaMetadataTui()
    preview_key = ("demo", "1.2.3", "py313h123_0", 0, "noarch", "demo.conda")
    calls: list[str] = []

    class _FakePathEntry:
        def __init__(self, relative_path: str) -> None:
            self.relative_path = relative_path

    class _FakePathsJson:
        paths = [
            _FakePathEntry("bin/demo"),
            _FakePathEntry("lib/python3.13/site-packages/demo.py"),
        ]

    async def _fake_from_remote_url(client: object, url: str) -> _FakePathsJson:
        del client
        calls.append(url)
        return _FakePathsJson()

    monkeypatch.setattr(
        "pixi_browse.tui.PathsJson.from_remote_url",
        _fake_from_remote_url,
    )

    url = "https://example.invalid/demo-1.2.3-py313h123_0.conda"
    paths = asyncio.run(app._get_package_paths(preview_key, url))
    cached_paths = asyncio.run(app._get_package_paths(preview_key, url))

    assert paths == [
        "bin/demo",
        "lib/python3.13/site-packages/demo.py",
    ]
    assert cached_paths == paths
    assert calls == [url]


def test_get_about_urls_caches_remote_about_json(monkeypatch) -> None:
    app = CondaMetadataTui()
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

    async def _fake_from_remote_url(client: object, url: str) -> _FakeAboutJson:
        del client
        calls.append(url)
        return _FakeAboutJson()

    async def _fake_fetch_raw_package_file_from_url(
        client: object, url: str, path: str
    ) -> bytes:
        del client
        assert url == "https://example.invalid/demo-1.2.3-py313h123_0.conda"
        assert path == "info/recipe/rendered_recipe.yaml"
        return b"system_tools:\n  rattler-build: 0.38.0\n"

    monkeypatch.setattr(
        "pixi_browse.tui.AboutJson.from_remote_url",
        _fake_from_remote_url,
    )
    monkeypatch.setattr(
        "pixi_browse.tui.fetch_raw_package_file_from_url",
        _fake_fetch_raw_package_file_from_url,
    )

    url = "https://example.invalid/demo-1.2.3-py313h123_0.conda"
    about_urls = asyncio.run(app._get_about_urls(preview_key, url))
    cached_about_urls = asyncio.run(app._get_about_urls(preview_key, url))

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
    assert calls == [url]


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

    assert CondaMetadataTui._extract_rattler_build_version(rendered_recipe) == "0.38.0"


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


def test_update_platform_selection_status_shows_all_selected_message(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._available_platform_names = [Platform("linux-64"), Platform("noarch")]
    app._selected_platform_names = {Platform("linux-64"), Platform("noarch")}

    class _FakeStatus:
        def __init__(self) -> None:
            self.messages: list[object] = []

        def update(self, value: object) -> None:
            self.messages.append(value)

    status = _FakeStatus()

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeStatus:
        assert selector == "#status"
        return status

    monkeypatch.setattr(app, "query_one", _fake_query_one)

    app._update_platform_selection_status()

    assert status.messages
    message = status.messages[-1]
    assert isinstance(message, Text)
    assert message.plain.endswith("All platforms selected")


def test_update_platform_selection_status_shows_select_all_shortcut(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._available_platform_names = [Platform("linux-64"), Platform("noarch")]
    app._selected_platform_names = {Platform("linux-64")}

    class _FakeStatus:
        def __init__(self) -> None:
            self.messages: list[object] = []

        def update(self, value: object) -> None:
            self.messages.append(value)

    status = _FakeStatus()

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeStatus:
        assert selector == "#status"
        return status

    monkeypatch.setattr(app, "query_one", _fake_query_one)

    app._update_platform_selection_status()

    assert status.messages
    message = status.messages[-1]
    assert isinstance(message, Text)
    assert message.plain.endswith("All platforms: a")
    assert not message.spans


def test_on_mount_applies_default_matchspec_after_loading_packages(monkeypatch) -> None:
    matchspec = MatchSpec("numpy >=2", exact_names_only=False)
    app = CondaMetadataTui(default_matchspec=matchspec)
    applied: list[MatchSpec | None] = []

    class _FakeOptionList:
        def __init__(self) -> None:
            self.disabled = False

        def focus(self) -> None:
            return None

    option_list = _FakeOptionList()

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeOptionList:
        assert selector == "#sidebar-list"
        return option_list

    async def _fake_load_packages() -> bool:
        return True

    async def _fake_apply_matchspec_query(value: MatchSpec | None) -> None:
        applied.append(value)

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(app, "_update_filter_indicator", lambda: None)
    monkeypatch.setattr(app, "_load_packages", _fake_load_packages)
    monkeypatch.setattr(app, "_apply_matchspec_query", _fake_apply_matchspec_query)

    asyncio.run(app.on_mount())

    assert option_list.disabled is True
    assert applied == [matchspec]


def test_open_versions_keeps_focus_in_sidebar(monkeypatch) -> None:
    app = CondaMetadataTui()
    focused: list[str] = []

    class _FakeOptionList:
        highlighted = 0
        scroll_y = 0.0

    option_list = _FakeOptionList()

    def _fake_query_one(selector: str, _widget_type: object = None) -> object:
        assert selector == "#sidebar-list"
        return option_list

    async def _fake_get_package_records(package_name: str) -> list[_Record]:
        assert package_name == "demo"
        return [
            _Record(
                version=Version("1.2.3"),
                build="py313h123_0",
                build_number=0,
                subdir="noarch",
                file_name="demo-1.2.3-py313h123_0.conda",
            )
        ]

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(app, "_get_package_records", _fake_get_package_records)
    monkeypatch.setattr(app, "_update_filter_indicator", lambda: None)
    monkeypatch.setattr(app, "_update_versions_status", lambda: None)
    monkeypatch.setattr(app, "_render_version_options", lambda: None)
    monkeypatch.setattr(app, "_focus_main_panel", lambda: focused.append("main-panel"))

    asyncio.run(app._open_versions("demo"))

    assert focused == []
    assert app._sidebar_title_text(selected=False).plain == "[0] Versions: demo"


def test_open_versions_uses_matchspec_records_when_present(monkeypatch) -> None:
    app = CondaMetadataTui()
    filtered_record = _make_repo_data_record(
        version="2.0.0",
        build="py313h999_0",
        build_number=0,
        subdir="linux-64",
        file_name="demo-2.0.0-py313h999_0.conda",
    )
    app._matchspec_records_by_package = {"demo": [filtered_record]}

    class _FakeOptionList:
        highlighted = 0
        scroll_y = 0.0

    option_list = _FakeOptionList()

    def _fake_query_one(selector: str, _widget_type: object = None) -> object:
        assert selector == "#sidebar-list"
        return option_list

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(app, "_update_filter_indicator", lambda: None)
    monkeypatch.setattr(app, "_update_versions_status", lambda: None)
    monkeypatch.setattr(app, "_render_version_options", lambda: None)

    async def _unexpected_get_package_records(
        package_name: str,
    ) -> list[RepoDataRecord]:
        raise AssertionError(f"unexpected full record lookup for {package_name}")

    monkeypatch.setattr(app, "_get_package_records", _unexpected_get_package_records)

    asyncio.run(app._open_versions("demo"))

    assert [entry.file_name for entry in app._current_versions] == [
        "demo-2.0.0-py313h999_0.conda"
    ]


def test_open_platform_selector_no_longer_queries_removed_sidebar_title(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._mode = "packages"

    class _FakeOptionList:
        highlighted = 3
        scroll_y = 7.0

    option_list = _FakeOptionList()

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeOptionList:
        assert selector == "#sidebar-list"
        return option_list

    rendered: list[str] = []
    statuses: list[str] = []
    indicators: list[str] = []

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(app, "_render_platform_options", lambda: rendered.append("ok"))
    monkeypatch.setattr(
        app, "_update_platform_selection_status", lambda: statuses.append("ok")
    )
    monkeypatch.setattr(
        app, "_update_platform_indicator", lambda: indicators.append("ok")
    )

    app._open_platform_selector()

    assert app._mode == "platforms"
    assert app._last_package_highlight == 3
    assert app._last_package_scroll_y == 7.0
    assert rendered == ["ok"]
    assert statuses == ["ok"]
    assert indicators == ["ok"]


def test_escape_from_main_panel_focuses_sidebar(monkeypatch) -> None:
    app = CondaMetadataTui()
    focused: list[str] = []
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: True)
    monkeypatch.setattr(app, "_focus_sidebar", lambda: focused.append("sidebar"))

    app.action_escape()

    assert focused == ["sidebar"]


def test_on_key_l_focuses_main_panel_from_sidebar(monkeypatch) -> None:
    app = CondaMetadataTui()
    focused: list[str] = []
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: True)
    monkeypatch.setattr(app, "_focus_main_panel", lambda: focused.append("main"))

    event = _FakeKeyEvent("l", "l")
    app.on_key(event)  # type: ignore[arg-type]

    assert focused == ["main"]
    assert event.stopped is True


def test_on_key_one_focuses_main_panel_in_packages_mode(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "packages"
    focused: list[str] = []

    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_focus_main_panel", lambda: focused.append("main"))

    event = _FakeKeyEvent("1", "1")
    app.on_key(event)  # type: ignore[arg-type]

    assert focused == ["main"]
    assert event.stopped is True


def test_on_key_one_focuses_main_panel_in_versions_preview(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    focused: list[str] = []

    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: False)
    monkeypatch.setattr(app, "_focus_main_panel", lambda: focused.append("main"))

    event = _FakeKeyEvent("1", "1")
    app.on_key(event)  # type: ignore[arg-type]

    assert focused == ["main"]
    assert event.stopped is True


def test_on_key_gg_jumps_sidebar_to_first(monkeypatch) -> None:
    app = CondaMetadataTui()
    jumped: list[str] = []
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: True)
    monkeypatch.setattr(app, "_jump_sidebar_first", lambda: jumped.append("first"))

    first_g = _FakeKeyEvent("g", "g")
    second_g = _FakeKeyEvent("g", "g")
    app.on_key(first_g)  # type: ignore[arg-type]
    app.on_key(second_g)  # type: ignore[arg-type]

    assert jumped == ["first"]
    assert first_g.stopped is True
    assert second_g.stopped is True


def test_on_key_ctrl_d_pages_sidebar(monkeypatch) -> None:
    app = CondaMetadataTui()
    page_calls: list[int] = []
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: True)
    monkeypatch.setattr(
        app, "_page_sidebar", lambda direction: page_calls.append(direction)
    )

    event = _FakeKeyEvent("ctrl+d")
    app.on_key(event)  # type: ignore[arg-type]

    assert page_calls == [1]
    assert event.stopped is True


def test_page_step_uses_visible_height() -> None:
    assert MainPanel._page_step(20) == 20
    assert MainPanel._page_step(7) == 7
    assert MainPanel._page_step(0) == 1


def test_set_sidebar_highlight_updates_version_preview(monkeypatch) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.33.1"),
        build="u64_idx_habc1234_1",
        build_number=1,
        subdir="osx-arm64",
        file_name="polars-1.33.1-u64_idx_habc1234_1.conda",
    )
    app._mode = "versions"
    app._selected_package = "polars"
    app._version_rows = [
        VersionRow(kind="section", subdir="osx-arm64"),
        VersionRow(kind="entry", subdir="osx-arm64", entry=entry),
    ]

    class _FakeOptionList:
        highlighted: int | None = None

    option_list = _FakeOptionList()
    preview_calls: list[tuple[str, VersionEntry]] = []

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeOptionList:
        assert selector == "#sidebar-list"
        return option_list

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(
        app,
        "_request_selected_version_preview",
        lambda package_name, version_entry: preview_calls.append(
            (package_name, version_entry)
        ),
    )

    app._set_sidebar_highlight(1)

    assert option_list.highlighted == 1
    assert preview_calls == [("polars", entry)]


def test_section_highlight_clears_preview_state_for_same_entry_revisit(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.33.1"),
        build="u64_idx_habc1234_1",
        build_number=1,
        subdir="osx-arm64",
        file_name="polars-1.33.1-u64_idx_habc1234_1.conda",
    )
    app._mode = "versions"
    app._selected_package = "polars"
    app._version_rows = [
        VersionRow(kind="section", subdir="osx-arm64"),
        VersionRow(kind="entry", subdir="osx-arm64", entry=entry),
    ]
    app._previewed_version_key = (
        "polars",
        "1.33.1",
        "u64_idx_habc1234_1",
        1,
        "osx-arm64",
        "polars-1.33.1-u64_idx_habc1234_1.conda",
    )
    updates: list[str] = []

    class _FakeOptionList:
        highlighted: int | None = None

    option_list = _FakeOptionList()

    def _fake_query_one(selector: str, _widget_type: object = None) -> object:
        assert selector == "#sidebar-list"
        return option_list

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(
        app, "_show_main_placeholder", lambda value: updates.append(value)
    )
    reset_calls: list[str] = []
    monkeypatch.setattr(
        app,
        "_reset_main_panel_scroll",
        lambda: reset_calls.append("reset"),
    )

    app._set_sidebar_highlight(0)

    assert app._previewed_version_key is None
    assert app._pending_preview_version_key is None
    assert updates[-1].startswith("# polars\n\nPlatform section: osx-arm64")
    assert reset_calls == ["reset"]


def test_request_selected_version_preview_resets_scroll_for_cached_details(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.33.1"),
        build="u64_idx_habc1234_1",
        build_number=1,
        subdir="osx-arm64",
        file_name="polars-1.33.1-u64_idx_habc1234_1.conda",
    )
    preview_key = (
        "polars",
        "1.33.1",
        "u64_idx_habc1234_1",
        1,
        "osx-arm64",
        "polars-1.33.1-u64_idx_habc1234_1.conda",
    )
    cached_details = VersionDetailsData(
        metadata_lines=("cached preview",),
        dependencies=("dep",),
        constraints=("constraint",),
        run_exports=("run export",),
        files=("file",),
    )
    app._version_details_cache = {preview_key: cached_details}
    updates: list[VersionDetailsData] = []
    reset_calls: list[str] = []
    monkeypatch.setattr(
        app, "_show_version_details", lambda value: updates.append(value)
    )
    monkeypatch.setattr(
        app,
        "_reset_main_panel_scroll",
        lambda: reset_calls.append("reset"),
    )

    app._request_selected_version_preview("polars", entry)

    assert updates == [cached_details]
    assert reset_calls == ["reset"]
    assert app._previewed_version_key == preview_key


def test_request_selected_version_preview_resets_scroll_for_uncached_details(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.33.2"),
        build="u64_idx_habc1234_2",
        build_number=2,
        subdir="osx-arm64",
        file_name="polars-1.33.2-u64_idx_habc1234_2.conda",
    )
    updates: list[str] = []
    reset_calls: list[str] = []
    worker_calls: list[dict[str, object]] = []

    def _fake_run_worker(coro: object, **kwargs: object) -> None:
        worker_calls.append(kwargs)
        coro.close()  # type: ignore[attr-defined]

    monkeypatch.setattr(
        app, "_show_main_placeholder", lambda value: updates.append(value)
    )
    monkeypatch.setattr(
        app,
        "_reset_main_panel_scroll",
        lambda: reset_calls.append("reset"),
    )
    monkeypatch.setattr(app, "run_worker", _fake_run_worker)

    app._request_selected_version_preview("polars", entry)

    assert updates == ["# polars 1.33.2\n\nLoading repodata for selected version..."]
    assert reset_calls == ["reset"]
    assert worker_calls == [
        {
            "group": "version-preview",
            "exclusive": True,
            "exit_on_error": False,
        }
    ]


def test_help_text_includes_expected_keybinds() -> None:
    app = CondaMetadataTui()

    help_text = app._help_text()

    assert "?                 Show this help" in help_text
    assert "j / k             Move selection or scroll" in help_text
    assert "h / l             Focus left / right pane" in help_text
    assert "1 / 2 / 3         Focus metadata, deps, or files" in help_text
    assert "Tab / Shift+Tab" in help_text
    assert "Cycle focused section" in help_text
    assert "[ / ]             Cycle dependency tabs" in help_text
    assert "Ctrl+u / Ctrl+d   Page up / down" in help_text
    assert "m                 Query MatchSpec" in help_text


def test_action_show_help_pushes_help_screen(monkeypatch) -> None:
    app = CondaMetadataTui()
    pushed: list[HelpScreen] = []

    monkeypatch.setattr(app, "push_screen", lambda screen: pushed.append(screen))

    app.action_show_help()

    assert len(pushed) == 1
    assert isinstance(pushed[0], HelpScreen)
    assert pushed[0]._title_text() == f"pixi-browse v{__version__}"


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


def test_matchspec_screen_updates_inline_error_message(monkeypatch) -> None:
    screen = MatchSpecScreen()

    class _FakeStatic:
        def __init__(self) -> None:
            self.updates: list[Text] = []

        def update(self, value: Text) -> None:
            self.updates.append(value)

    error_widget = _FakeStatic()

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeStatic:
        assert selector == "#matchspec-error"
        return error_widget

    monkeypatch.setattr(screen, "query_one", _fake_query_one)

    screen._update_validation_error("numpy[")
    screen._update_validation_error("python*")

    assert error_widget.updates[0].plain != ""
    assert error_widget.updates[-1].plain == ""


def test_action_matchspec_key_m_pushes_matchspec_screen(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._matchspec_query = "numpy >=2"
    pushed: list[tuple[MatchSpecScreen, object | None]] = []

    monkeypatch.setattr(
        app,
        "push_screen",
        lambda screen, callback=None: pushed.append((screen, callback)),
    )

    app.action_matchspec_key_m()

    assert len(pushed) == 1
    screen, callback = pushed[0]
    assert isinstance(screen, MatchSpecScreen)
    assert screen._initial_value == "numpy >=2"
    assert screen._select_on_focus is True
    assert callback == app._handle_matchspec_result


def test_handle_matchspec_result_queues_matchspec_worker(monkeypatch) -> None:
    app = CondaMetadataTui()
    worker_calls: list[dict[str, object]] = []

    def _fake_run_worker(coro: object, **kwargs: object) -> None:
        worker_calls.append(kwargs)
        coro.close()  # type: ignore[attr-defined]

    monkeypatch.setattr(app, "run_worker", _fake_run_worker)

    app._handle_matchspec_result(MatchSpec("numpy >=2", exact_names_only=False))

    assert worker_calls == [
        {
            "group": "matchspec-selection",
            "exclusive": True,
            "exit_on_error": False,
        }
    ]


def test_handle_matchspec_result_ignores_cancel(monkeypatch) -> None:
    app = CondaMetadataTui()
    calls: list[object] = []

    monkeypatch.setattr(app, "run_worker", lambda *args, **kwargs: calls.append(args))

    app._handle_matchspec_result(None)

    assert calls == []


def test_action_open_external_url_uses_webbrowser(monkeypatch) -> None:
    app = CondaMetadataTui()
    opened: list[str] = []

    def _fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("webbrowser.open", _fake_open)

    app.action_open_external_url("https://example.com/demo")

    assert opened == ["https://example.com/demo"]


def test_rerender_visible_version_preview_requests_fresh_preview_when_not_cached(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )
    app._mode = "versions"
    app._selected_package = "demo"
    app._version_rows = [VersionRow(kind="entry", subdir="noarch", entry=entry)]
    stale_cached_details = VersionDetailsData(
        metadata_lines=("cached",),
        dependencies=("dep",),
        constraints=("constraint",),
        run_exports=("export",),
        files=("file",),
    )
    app._version_details_cache = {
        ("demo", "1.2.3", "py313h123_0", 0, "noarch", "old"): stale_cached_details
    }

    class _FakeOptionList:
        highlighted = 0

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeOptionList:
        assert selector == "#sidebar-list"
        return _FakeOptionList()

    preview_calls: list[tuple[str, VersionEntry]] = []

    def _fake_request_preview(package_name: str, version_entry: VersionEntry) -> None:
        preview_calls.append((package_name, version_entry))

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(app, "_request_selected_version_preview", _fake_request_preview)

    app._rerender_visible_version_preview()

    assert app._version_details_cache == {
        ("demo", "1.2.3", "py313h123_0", 0, "noarch", "old"): stale_cached_details
    }
    assert preview_calls == [("demo", entry)]


def test_rerender_visible_version_preview_uses_cached_details(monkeypatch) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )
    preview_key = (
        "demo",
        "1.2.3",
        "py313h123_0",
        0,
        "noarch",
        "demo-1.2.3-py313h123_0.conda",
    )
    cached_details = VersionDetailsData(
        metadata_lines=("cached",),
        dependencies=("dep",),
        constraints=("constraint",),
        run_exports=("export",),
        files=("file",),
    )
    app._mode = "versions"
    app._selected_package = "demo"
    app._version_rows = [VersionRow(kind="entry", subdir="noarch", entry=entry)]
    app._version_details_cache = {preview_key: cached_details}
    shown: list[VersionDetailsData] = []

    class _FakeOptionList:
        highlighted = 0

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeOptionList:
        assert selector == "#sidebar-list"
        return _FakeOptionList()

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(app, "_show_version_details", lambda value: shown.append(value))

    app._rerender_visible_version_preview()

    assert shown == [cached_details]
    assert app._previewed_version_key == preview_key
    assert app._pending_preview_version_key == preview_key


def test_selecting_version_entry_keeps_focus_in_sidebar(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    app._selected_package = "demo"
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )
    app._version_rows = [VersionRow(kind="entry", subdir="noarch", entry=entry)]
    preview_calls: list[tuple[str, VersionEntry]] = []
    focused: list[str] = []

    monkeypatch.setattr(
        app,
        "_request_selected_version_preview",
        lambda package_name, version: preview_calls.append((package_name, version)),
    )
    monkeypatch.setattr(app, "_focus_main_panel", lambda: focused.append("main"))

    class _FakeOptionList:
        id = "sidebar-list"

    class _FakeEvent:
        def __init__(self) -> None:
            self.option_list = _FakeOptionList()
            self.option_index = 0

    event = _FakeEvent()
    asyncio.run(app.on_option_list_option_selected(event))  # type: ignore[arg-type]

    assert preview_calls == [("demo", entry)]
    assert focused == []


def test_selecting_version_entry_with_keyboard_focuses_main_panel(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    app._selected_package = "demo"
    app._sidebar_selection_by_keyboard = True
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )
    app._version_rows = [VersionRow(kind="entry", subdir="noarch", entry=entry)]
    preview_calls: list[tuple[str, VersionEntry]] = []
    focused: list[str] = []

    monkeypatch.setattr(
        app,
        "_request_selected_version_preview",
        lambda package_name, version: preview_calls.append((package_name, version)),
    )
    monkeypatch.setattr(app, "_focus_main_panel", lambda: focused.append("main"))

    class _FakeOptionList:
        id = "sidebar-list"

    class _FakeEvent:
        def __init__(self) -> None:
            self.option_list = _FakeOptionList()
            self.option_index = 0

    event = _FakeEvent()
    asyncio.run(app.on_option_list_option_selected(event))  # type: ignore[arg-type]

    assert preview_calls == [("demo", entry)]
    assert focused == ["main"]


def test_selecting_dependency_option_opens_matchspec_screen(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    opened: list[str] = []
    focused: list[str] = []
    sections: list[int] = []

    monkeypatch.setattr(app, "_dependency_matchspec_at", lambda index: "python >=3.12")
    monkeypatch.setattr(
        app, "_defer_matchspec_screen", lambda value: opened.append(value)
    )
    monkeypatch.setattr(app, "_focus_main_panel", lambda: focused.append("main"))
    monkeypatch.setattr(
        app, "_set_active_main_section", lambda value: sections.append(value)
    )

    class _FakeOptionList:
        id = "detail-option-list-1"

    class _FakeEvent:
        def __init__(self) -> None:
            self.option_list = _FakeOptionList()
            self.option_index = 0

    event = _FakeEvent()
    asyncio.run(app.on_option_list_option_selected(event))  # type: ignore[arg-type]

    assert sections == [1]
    assert focused == ["main"]
    assert opened == ["python >=3.12"]


def test_on_key_numeric_shortcut_focuses_main_section(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    focused: list[str] = []
    selected_sections: list[int] = []

    monkeypatch.setattr(
        app, "_set_active_main_section", lambda value: selected_sections.append(value)
    )
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: True)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: False)
    monkeypatch.setattr(app, "_focus_main_panel", lambda: focused.append("main"))

    event = _FakeKeyEvent("2", "2")
    app.on_key(event)  # type: ignore[arg-type]

    assert selected_sections == [1]
    assert focused == ["main"]
    assert event.stopped is True


def test_on_key_zero_focuses_sidebar_in_versions_mode(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    focused: list[str] = []

    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: True)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: False)
    monkeypatch.setattr(app, "_focus_sidebar", lambda: focused.append("sidebar"))

    event = _FakeKeyEvent("0", "0")
    app.on_key(event)  # type: ignore[arg-type]

    assert focused == ["sidebar"]
    assert event.stopped is True


def test_on_key_zero_focuses_sidebar_in_packages_mode(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "packages"
    focused: list[str] = []

    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_focus_sidebar", lambda: focused.append("sidebar"))

    event = _FakeKeyEvent("0", "0")
    app.on_key(event)  # type: ignore[arg-type]

    assert focused == ["sidebar"]
    assert event.stopped is True


def test_on_key_bracket_shortcut_cycles_dependency_tab(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    app._selected_pane = "main"
    focused: list[str] = []
    tab_directions: list[int] = []

    class _FakeMainPanel:
        def dependency_section_is_active(self) -> bool:
            return True

    monkeypatch.setattr(
        app,
        "_cycle_main_dependency_tab",
        lambda value: tab_directions.append(value),
    )
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: True)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: True)
    monkeypatch.setattr(app, "_focus_main_panel", lambda: focused.append("main"))
    monkeypatch.setattr(
        app,
        "query_one",
        lambda selector, _widget_type=None: _FakeMainPanel(),
    )

    event = _FakeKeyEvent("]", "]")
    app.on_key(event)  # type: ignore[arg-type]

    assert tab_directions == [1]
    assert focused == ["main"]
    assert event.stopped is True


def test_dependency_header_tabs_are_clickable() -> None:
    text = VersionDetailsView._render_clickable_dependency_tab(
        "constraints",
        "Constraints (1)",
        active=False,
        pane_active=False,
    )

    assert text.plain == "Constraints (1)"
    assert any(
        span.style.meta == {"@click": ("app.select_dependency_tab", ("constraints",))}
        for span in text.spans
        if isinstance(span.style, Style)
    )
    assert any(
        span.style == INACTIVE_TAB_STYLE
        for span in text.spans
        if isinstance(span.style, Style)
        and span.style.meta
        != {"@click": ("app.select_dependency_tab", ("constraints",))}
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
        and span.style.meta
        != {"@click": ("app.select_dependency_tab", ("constraints",))}
    )


def test_dependency_header_hint_is_only_shown_for_active_pane() -> None:
    view = VersionDetailsView()
    view._pane_selected = False

    view._active_section = 0
    inactive_header = view._render_dependency_header()

    view._pane_selected = True
    view._active_section = 1
    active_header = view._render_dependency_header()

    assert "[ / ]" not in inactive_header.plain
    assert "[ / ]" not in active_header.plain


def test_dependency_header_keeps_selected_tab_colored_when_pane_is_inactive() -> None:
    view = VersionDetailsView()
    view._active_section = 0
    view._dependency_tab_index = 1
    view._details = VersionDetailsData(
        metadata_lines=("meta",),
        dependencies=("dep",),
        constraints=("constraint",),
        run_exports=("run export",),
        files=("file",),
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
    view._details = VersionDetailsData(
        metadata_lines=("meta",),
        dependencies=("dep",),
        constraints=("constraint",),
        run_exports=("run export",),
        files=("file",),
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
    view._details = VersionDetailsData(
        metadata_lines=("meta",),
        dependencies=("dep",),
        constraints=(),
        run_exports=(),
        files=("file",),
    )

    header = view._render_dependency_header()

    assert header.style == ACTIVE_SECTION_TITLE_STYLE


def test_dependency_header_shows_zero_counts_when_sections_are_empty() -> None:
    view = VersionDetailsView()
    view._details = VersionDetailsData(
        metadata_lines=("meta",),
        dependencies=(),
        constraints=(),
        run_exports=(),
        files=("file",),
    )

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
    view._details = VersionDetailsData(
        metadata_lines=("meta",),
        dependencies=(),
        constraints=(),
        run_exports=("weak: python_abi 3.13.* *_cp313",),
        files=("file",),
    )

    entries = view._dependency_entries_for_tab("run_exports")

    assert entries[0].label == "weak: python_abi 3.13.* *_cp313"
    assert entries[0].matchspec == "python_abi 3.13.* *_cp313"


def test_dependency_list_entry_unescapes_matchspec_text() -> None:
    view = VersionDetailsView()
    view._details = VersionDetailsData(
        metadata_lines=("meta",),
        dependencies=(r"demo \[version='>=1'\]",),
        constraints=(),
        run_exports=(),
        files=("file",),
    )

    entries = view._dependency_entries_for_tab("dependencies")

    assert entries[0].label == "demo [version='>=1']"
    assert entries[0].matchspec == "demo [version='>=1']"


def test_file_list_entry_uses_plain_file_path() -> None:
    view = VersionDetailsView()
    view._details = VersionDetailsData(
        metadata_lines=("meta",),
        dependencies=(),
        constraints=(),
        run_exports=(),
        files=("site-packages/demo.py",),
        file_paths=("site-packages/demo.py",),
    )

    entries = view._file_entries_for_details()

    assert entries[0].label == "site-packages/demo.py"
    assert entries[0].path == "site-packages/demo.py"


def test_on_key_bracket_shortcut_is_ignored_when_dependency_pane_is_inactive(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    app._selected_pane = "main"

    class _FakeMainPanel:
        def dependency_section_is_active(self) -> bool:
            return False

    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: True)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: True)
    monkeypatch.setattr(
        app,
        "query_one",
        lambda selector, _widget_type=None: _FakeMainPanel(),
    )

    event = _FakeKeyEvent("]", "]")
    app.on_key(event)  # type: ignore[arg-type]

    assert event.stopped is False


def test_on_key_bracket_shortcut_is_ignored_when_sidebar_is_selected(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    app._selected_pane = "sidebar"
    cycled: list[int] = []

    class _FakeMainPanel:
        def dependency_section_is_active(self) -> bool:
            return True

    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: True)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: True)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: False)
    monkeypatch.setattr(
        app,
        "_cycle_main_dependency_tab",
        lambda value: cycled.append(value),
    )
    monkeypatch.setattr(
        app,
        "query_one",
        lambda selector, _widget_type=None: _FakeMainPanel(),
    )

    event = _FakeKeyEvent("[", "[")
    app.on_key(event)  # type: ignore[arg-type]

    assert cycled == []
    assert event.stopped is False


def test_sidebar_highlight_does_not_switch_selected_pane_without_sidebar_focus(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._selected_pane = "main"
    highlighted_updates: list[int] = []

    class _FakeOptionList:
        id = "sidebar-list"

    class _FakeEvent:
        def __init__(self) -> None:
            self.option_list = _FakeOptionList()
            self.option_index = 3

    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(
        app,
        "_update_main_panel_for_sidebar_highlight",
        lambda option_index: highlighted_updates.append(option_index),
    )
    monkeypatch.setattr(
        app,
        "_update_filter_indicator",
        lambda: (_ for _ in ()).throw(AssertionError("should not update pane state")),
    )

    event = _FakeEvent()
    app.on_option_list_option_highlighted(event)  # type: ignore[arg-type]

    assert app._selected_pane == "main"
    assert highlighted_updates == [3]


def test_on_key_tab_shortcut_cycles_main_section(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    section_directions: list[int] = []

    monkeypatch.setattr(
        app,
        "_cycle_active_main_section",
        lambda value: section_directions.append(value),
    )
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: True)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: True)

    event = _FakeKeyEvent("tab")
    app.on_key(event)  # type: ignore[arg-type]

    assert section_directions == [1]
    assert event.stopped is True


def test_on_key_enter_opens_matchspec_screen_for_selected_dependency(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    opened: list[str] = []

    class _FakeMainPanel:
        def dependency_section_is_active(self) -> bool:
            return True

    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: True)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: True)
    monkeypatch.setattr(app, "_selected_dependency_matchspec", lambda: "numpy >=2")
    monkeypatch.setattr(
        app, "_defer_matchspec_screen", lambda value: opened.append(value)
    )
    monkeypatch.setattr(
        app,
        "query_one",
        lambda selector, _widget_type=None: _FakeMainPanel(),
    )

    event = _FakeKeyEvent("enter")
    app.on_key(event)  # type: ignore[arg-type]

    assert opened == ["numpy >=2"]
    assert event.stopped is True


def test_on_key_enter_opens_file_action_screen_for_selected_file(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    opened: list[str] = []

    class _FakeMainPanel:
        def dependency_section_is_active(self) -> bool:
            return False

        def file_section_is_active(self) -> bool:
            return True

    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: True)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: True)
    monkeypatch.setattr(
        app,
        "_request_file_action_for_selected_file",
        lambda: opened.append("file"),
    )
    monkeypatch.setattr(
        app,
        "query_one",
        lambda selector, _widget_type=None: _FakeMainPanel(),
    )

    event = _FakeKeyEvent("enter")
    app.on_key(event)  # type: ignore[arg-type]

    assert opened == ["file"]
    assert event.stopped is True


def test_defer_matchspec_screen_waits_until_after_refresh(monkeypatch) -> None:
    app = CondaMetadataTui()
    opened: list[tuple[str, bool]] = []
    scheduled: list[object] = []

    monkeypatch.setattr(
        app,
        "_open_matchspec_screen",
        lambda value, *, select_on_focus=True: opened.append((value, select_on_focus)),
    )
    monkeypatch.setattr(
        app, "call_after_refresh", lambda callback: scheduled.append(callback)
    )

    app._defer_matchspec_screen("numpy >=2")

    assert opened == []
    assert len(scheduled) == 1

    callback = scheduled[0]
    assert callable(callback)
    callback()

    assert opened == [("numpy >=2", False)]


def test_defer_file_action_screen_waits_until_after_refresh(monkeypatch) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )
    opened: list[tuple[str, str, str]] = []
    scheduled: list[object] = []

    monkeypatch.setattr(
        app,
        "_open_file_action_screen",
        lambda package_name, selected_entry, file_path: opened.append(
            (package_name, selected_entry.file_name, file_path)
        ),
    )
    monkeypatch.setattr(
        app, "call_after_refresh", lambda callback: scheduled.append(callback)
    )

    app._defer_file_action_screen("demo", entry, "info/about.json")

    assert opened == []
    assert len(scheduled) == 1

    callback = scheduled[0]
    assert callable(callback)
    callback()

    assert opened == [("demo", entry.file_name, "info/about.json")]


def test_on_key_shift_tab_shortcut_cycles_main_section_backwards(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    section_directions: list[int] = []

    monkeypatch.setattr(
        app,
        "_cycle_active_main_section",
        lambda value: section_directions.append(value),
    )
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: True)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: True)

    event = _FakeKeyEvent("shift+tab")
    app.on_key(event)  # type: ignore[arg-type]

    assert section_directions == [-1]
    assert event.stopped is True


def test_on_key_tab_does_nothing_when_sidebar_is_active(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    section_directions: list[int] = []

    monkeypatch.setattr(
        app,
        "_cycle_active_main_section",
        lambda value: section_directions.append(value),
    )
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: False)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: False)

    event = _FakeKeyEvent("tab")
    app.on_key(event)  # type: ignore[arg-type]

    assert section_directions == []
    assert event.stopped is True


def test_on_key_tab_does_not_switch_panes_when_details_exist_but_main_is_inactive(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    section_directions: list[int] = []

    monkeypatch.setattr(
        app,
        "_cycle_active_main_section",
        lambda value: section_directions.append(value),
    )
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: True)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: True)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: False)

    event = _FakeKeyEvent("tab")
    app.on_key(event)  # type: ignore[arg-type]

    assert section_directions == []
    assert event.stopped is True


def test_on_key_tab_does_nothing_in_versions_preview_with_main_focus(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    section_directions: list[int] = []

    monkeypatch.setattr(
        app,
        "_cycle_active_main_section",
        lambda value: section_directions.append(value),
    )
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(app, "_main_panel_shows_version_details", lambda: False)
    monkeypatch.setattr(app, "_main_panel_is_focused", lambda: True)

    event = _FakeKeyEvent("tab")
    app.on_key(event)  # type: ignore[arg-type]

    assert section_directions == []
    assert event.stopped is True


def test_action_select_dependency_tab_focuses_dependency_pane(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    focused: list[str] = []
    sections: list[int] = []
    tabs: list[str] = []

    monkeypatch.setattr(
        app, "_set_active_main_section", lambda value: sections.append(value)
    )
    monkeypatch.setattr(
        app, "_set_main_dependency_tab", lambda value: tabs.append(value)
    )
    monkeypatch.setattr(app, "_focus_main_panel", lambda: focused.append("main"))

    app.action_select_dependency_tab("constraints")

    assert sections == [1]
    assert tabs == ["constraints"]
    assert focused == ["main"]


def test_activate_section_focuses_main_panel(monkeypatch) -> None:
    view = VersionDetailsView()
    active_sections: list[int] = []
    focused: list[str] = []

    class _FakeMainPanel:
        def focus(self) -> None:
            focused.append("main")

    class _FakeApp:
        def query_one(
            self, selector: str, _widget_type: object = None
        ) -> _FakeMainPanel:
            assert selector == "#main-panel"
            return _FakeMainPanel()

    monkeypatch.setattr(
        VersionDetailsView,
        "app",
        property(lambda self: _FakeApp()),
    )
    monkeypatch.setattr(
        view,
        "set_active_section",
        lambda value: active_sections.append(value),
    )

    view.activate_section(2, focus_main_panel=True)

    assert active_sections == [2]
    assert focused == ["main"]


def test_select_dependency_tab_focuses_main_panel(monkeypatch) -> None:
    view = VersionDetailsView()
    active_sections: list[int] = []
    tabs: list[str] = []
    focused: list[str] = []

    class _FakeMainPanel:
        def focus(self) -> None:
            focused.append("main")

    class _FakeApp:
        def query_one(
            self, selector: str, _widget_type: object = None
        ) -> _FakeMainPanel:
            assert selector == "#main-panel"
            return _FakeMainPanel()

    monkeypatch.setattr(
        VersionDetailsView,
        "app",
        property(lambda self: _FakeApp()),
    )
    monkeypatch.setattr(
        view,
        "set_active_section",
        lambda value: active_sections.append(value),
    )
    monkeypatch.setattr(view, "set_dependency_tab", lambda value: tabs.append(value))

    view.select_dependency_tab("constraints", focus_main_panel=True)

    assert active_sections == [1]
    assert tabs == ["constraints"]
    assert focused == ["main"]


def test_option_list_selection_opens_file_action_screen(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._selected_package = "demo"
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )
    opened: list[tuple[str, str]] = []
    sections: list[int] = []
    focused: list[str] = []

    class _FakeOptionList:
        id = "sidebar-list"
        highlighted = 0

    class _FakeEvent:
        def __init__(self) -> None:
            self.option_list = type(
                "OptionListEvent", (), {"id": "detail-option-list-2"}
            )()
            self.option_index = 0

    monkeypatch.setattr(app, "_file_path_at", lambda index: "info/about.json")
    monkeypatch.setattr(app, "_highlighted_version_entry", lambda: entry)
    monkeypatch.setattr(
        app, "_set_active_main_section", lambda value: sections.append(value)
    )
    monkeypatch.setattr(app, "_focus_main_panel", lambda: focused.append("main"))
    monkeypatch.setattr(
        app,
        "_defer_file_action_screen",
        lambda package_name, selected_entry, file_path: opened.append(
            (package_name, file_path)
        ),
    )
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)
    monkeypatch.setattr(
        app,
        "query_one",
        lambda selector, _widget_type=None: _FakeOptionList(),
    )

    event = _FakeEvent()
    asyncio.run(app.on_option_list_option_selected(event))  # type: ignore[arg-type]

    assert sections == [2]
    assert focused == ["main"]
    assert opened == [("demo", "info/about.json")]


def test_clicking_detail_section_activates_and_focuses_pane(monkeypatch) -> None:
    section = DetailSection("Files", 2)
    activated: list[tuple[int, bool]] = []

    class _FakeView:
        def activate_section(
            self, index: int, *, focus_main_panel: bool = False
        ) -> None:
            activated.append((index, focus_main_panel))

    class _FakeApp:
        def query_one(self, selector: str, _widget_type: object = None) -> _FakeView:
            assert selector == "#version-details-view"
            return _FakeView()

    monkeypatch.setattr(
        DetailSection,
        "app",
        property(lambda self: _FakeApp()),
    )

    event = _FakeClickEvent()
    section.on_click(event)  # type: ignore[arg-type]

    assert activated == [(2, True)]
    assert event.stopped is True


def test_clicking_dependency_tab_dispatches_without_hover_link_action(
    monkeypatch,
) -> None:
    section = DetailSection("Dependencies", 1)
    selected_tabs: list[tuple[str, bool]] = []

    class _FakeView:
        def select_dependency_tab(
            self, tab: str, *, focus_main_panel: bool = False
        ) -> None:
            selected_tabs.append((tab, focus_main_panel))

    class _FakeApp:
        def query_one(self, selector: str, _widget_type: object = None) -> _FakeView:
            assert selector == "#version-details-view"
            return _FakeView()

    monkeypatch.setattr(
        DetailSection,
        "app",
        property(lambda self: _FakeApp()),
    )

    event = _FakeClickEvent(
        Style(
            meta={
                "@click": (
                    "app.select_dependency_tab",
                    ("constraints",),
                )
            }
        )
    )
    section.on_click(event)  # type: ignore[arg-type]

    assert selected_tabs == [("constraints", False)]
    assert event.stopped is True


def test_clicking_main_panel_focuses_it(monkeypatch) -> None:
    panel = MainPanel()
    focused: list[str] = []
    selected: list[str] = []

    class _FakeApp:
        def _set_selected_pane(self, pane: str) -> None:
            selected.append(pane)

    monkeypatch.setattr(
        MainPanel,
        "app",
        property(lambda self: _FakeApp()),
    )

    monkeypatch.setattr(panel, "focus", lambda: focused.append("main"))

    event = _FakeClickEvent()
    panel.on_click(event)  # type: ignore[arg-type]

    assert selected == ["main"]
    assert focused == ["main"]
    assert event.stopped is True


def test_clicking_sidebar_panel_focuses_sidebar_list(monkeypatch) -> None:
    panel = SidebarPanel()
    focused: list[str] = []
    selected: list[str] = []

    class _FakeSidebarList:
        def focus(self) -> None:
            focused.append("sidebar")

    class _FakeApp:
        def _set_selected_pane(self, pane: str) -> None:
            selected.append(pane)

        def query_one(
            self, selector: str, _widget_type: object = None
        ) -> _FakeSidebarList:
            assert selector == "#sidebar-list"
            return _FakeSidebarList()

    monkeypatch.setattr(
        SidebarPanel,
        "app",
        property(lambda self: _FakeApp()),
    )

    event = _FakeClickEvent()
    panel.on_click(event)  # type: ignore[arg-type]

    assert selected == ["sidebar"]
    assert focused == ["sidebar"]
    assert event.stopped is True


def test_main_panel_h_key_uses_app_sidebar_focus(monkeypatch) -> None:
    panel = MainPanel()
    focused: list[str] = []

    class _FakeApp:
        def _focus_sidebar(self) -> None:
            focused.append("sidebar")

    monkeypatch.setattr(
        MainPanel,
        "app",
        property(lambda self: _FakeApp()),
    )
    monkeypatch.setattr(panel, "_showing_version_details", lambda: False)
    monkeypatch.setattr(panel, "current_page_step", lambda: 10)

    event = _FakeKeyEvent("h", "h")
    panel.on_key(event)  # type: ignore[arg-type]

    assert focused == ["sidebar"]
    assert event.stopped is True


def test_update_versions_status_shows_toggle_hint(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._current_versions = [
        VersionEntry(
            version=Version("1.2.3"),
            build="py313h123_0",
            build_number=0,
            subdir="noarch",
            file_name="demo-1.2.3-py313h123_0.conda",
        )
    ]
    app._version_subdirs = ["noarch"]
    updates: list[Text | str] = []

    class _FakeStatus:
        def update(self, value: Text | str) -> None:
            updates.append(value)

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeStatus:
        assert selector == "#status"
        return _FakeStatus()

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    app._update_versions_status()

    assert len(updates) == 1
    assert updates == ["1 entries across 1 platform."]


def test_update_download_indicator_in_versions_mode(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"

    class _FakeStyles:
        border_subtitle_align = "right"

    class _FakeMainPanel:
        def __init__(self) -> None:
            self.styles = _FakeStyles()
            self.border_title: Text | str = ""
            self.border_subtitle: Text | str = ""

        def showing_version_details(self) -> bool:
            return False

    main_panel = _FakeMainPanel()

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeMainPanel:
        assert selector == "#main-panel"
        return main_panel

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    app._update_download_indicator()

    assert main_panel.styles.border_subtitle_align == "right"
    assert main_panel.border_title == ""
    assert main_panel.border_subtitle == ""


def test_update_download_indicator_cleared_outside_versions(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "packages"

    class _FakeStyles:
        border_subtitle_align = "right"

    class _FakeMainPanel:
        def __init__(self) -> None:
            self.styles = _FakeStyles()
            self.border_title: Text | str = ""
            self.border_subtitle: Text | str = "existing"

        def showing_version_details(self) -> bool:
            return False

    main_panel = _FakeMainPanel()

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeMainPanel:
        assert selector == "#main-panel"
        return main_panel

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    app._update_download_indicator()

    assert main_panel.border_title == ""
    assert main_panel.styles.border_subtitle_align == "right"
    assert main_panel.border_subtitle == ""


def test_action_channel_key_c_appends_filter_char_in_filter_mode(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "packages"
    app._filter_mode = True
    appended: list[str] = []

    def _fake_append_filter_char(char: str) -> None:
        appended.append(char)

    monkeypatch.setattr(app, "_append_filter_char", _fake_append_filter_char)

    app.action_channel_key_c()

    assert appended == ["c"]


def test_on_key_f_appends_filter_text_when_filter_mode_is_active(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "packages"
    app._filter_mode = True
    app._search_query = ""
    filtered: list[str] = []
    updated: list[str] = []

    monkeypatch.setattr(app, "_filter_packages", lambda: filtered.append("filtered"))
    monkeypatch.setattr(
        app, "_update_filter_indicator", lambda: updated.append("updated")
    )
    monkeypatch.setattr(app, "_sidebar_is_focused", lambda: False)

    event = _FakeKeyEvent("f", "f")
    app.on_key(event)  # type: ignore[arg-type]

    assert app._search_query == "f"
    assert filtered == ["filtered"]
    assert updated == ["updated"]
    assert event.stopped is True


def test_action_channel_key_c_starts_channel_edit_mode() -> None:
    app = CondaMetadataTui()
    app._mode = "packages"
    app._filter_mode = False
    app._channel_name = "custom-channel"
    app._channel_draft = "stale-draft"
    app._update_filter_indicator = lambda: None  # type: ignore[method-assign]

    app.action_channel_key_c()

    assert app._channel_edit_mode is True
    assert app._channel_draft == "custom-channel"


def test_confirm_channel_edit_queues_channel_reload_worker(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._channel_edit_mode = True
    app._channel_draft = "my-channel"
    app._update_filter_indicator = lambda: None  # type: ignore[method-assign]
    worker_calls: list[dict[str, object]] = []

    def _fake_run_worker(coro: object, **kwargs: object) -> None:
        worker_calls.append(kwargs)
        coro.close()  # type: ignore[attr-defined]

    monkeypatch.setattr(app, "run_worker", _fake_run_worker)

    app._confirm_channel_edit()

    assert worker_calls == [
        {
            "group": "channel-selection",
            "exclusive": True,
            "exit_on_error": False,
        }
    ]


def test_apply_channel_selection_renders_sidebar_loading_placeholder(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._channel_name = "conda-forge"

    class _FakeOptionList:
        def __init__(self) -> None:
            self.disabled = False
            self.options: list[str] = []
            self.highlighted: int | None = None
            self.scroll_y = 0.0
            self.focused = False

        def clear_options(self) -> None:
            self.options.clear()

        def add_option(self, option: str) -> None:
            self.options.append(option)

        def focus(self) -> None:
            self.focused = True

    option_list = _FakeOptionList()
    placeholder_updates: list[str] = []
    notifications: list[str] = []
    restored: list[object] = []

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeOptionList:
        assert selector == "#sidebar-list"
        return option_list

    async def _fake_load_packages() -> bool:
        assert option_list.options == ["Loading packages..."]
        assert option_list.highlighted == 0
        assert option_list.disabled is True
        return False

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(app, "_snapshot_channel_state", lambda: "snapshot")
    monkeypatch.setattr(app, "_clear_channel_loaded_state", lambda: None)
    monkeypatch.setattr(app, "_show_main_placeholder", placeholder_updates.append)
    monkeypatch.setattr(app, "_update_filter_indicator", lambda: None)
    monkeypatch.setattr(app, "_load_packages", _fake_load_packages)
    monkeypatch.setattr(app, "_restore_channel_state", restored.append)
    monkeypatch.setattr(app, "_restore_ui_from_snapshot", restored.append)
    monkeypatch.setattr(
        app, "notify", lambda message, **kwargs: notifications.append(message)
    )

    import asyncio

    asyncio.run(app._apply_channel_selection("prefix.dev/conda-forge"))

    assert placeholder_updates == ["# prefix.dev/conda-forge\n\nLoading repodata..."]
    assert restored == ["snapshot", "snapshot"]
    assert notifications == ["Failed to load channel: prefix.dev/conda-forge"]
    assert option_list.focused is True


def test_request_package_preview_uses_matchspec_records(monkeypatch) -> None:
    app = CondaMetadataTui()
    record = _make_repo_data_record(name="demo")
    app._matchspec_records_by_package = {"demo": [record]}
    previewed: list[tuple[str, list[RepoDataRecord]]] = []

    monkeypatch.setattr(
        app,
        "_update_main_panel_for_package",
        lambda package_name, records: previewed.append((package_name, records)),
    )
    monkeypatch.setattr(
        app,
        "run_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("worker should not run for cached MatchSpec records")
        ),
    )

    app._request_package_preview("demo")

    assert previewed == [("demo", [record])]


def test_apply_matchspec_query_empty_restores_full_package_selection(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._channel_package_names = ["demo", "numpy"]
    app._matchspec_query = "demo >=1"
    app._matchspec_records_by_package = {"demo": [_make_repo_data_record(name="demo")]}
    app._mode = "versions"
    filtered: list[str] = []
    updated: list[str] = []
    focused: list[str] = []

    monkeypatch.setattr(app, "_filter_packages", lambda: filtered.append("filtered"))
    monkeypatch.setattr(
        app, "_update_filter_indicator", lambda: updated.append("updated")
    )
    monkeypatch.setattr(app, "_focus_sidebar", lambda: focused.append("sidebar"))

    asyncio.run(app._apply_matchspec_query(None))

    assert app._matchspec_query == ""
    assert app._matchspec_records_by_package == {}
    assert app._all_package_names == ["demo", "numpy"]
    assert app._mode == "packages"
    assert filtered == ["filtered"]
    assert updated == ["updated"]
    assert focused == ["sidebar"]


def test_apply_matchspec_result_auto_opens_versions_for_single_package(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    record = _make_repo_data_record(name="demo", version="2.0.0")
    opened: list[str] = []
    focused: list[str] = []

    monkeypatch.setattr(
        app,
        "_filter_packages",
        lambda: setattr(app, "_visible_package_names", list(app._all_package_names)),
    )
    monkeypatch.setattr(app, "_update_filter_indicator", lambda: None)
    monkeypatch.setattr(app, "_focus_sidebar", lambda: focused.append("sidebar"))

    async def _fake_open_versions(package_name: str) -> None:
        opened.append(package_name)

    monkeypatch.setattr(app, "_open_versions", _fake_open_versions)

    asyncio.run(
        app._apply_matchspec_result(
            "demo >=2",
            MatchSpecQueryResult(
                package_names=["demo"],
                records_by_package={"demo": [record]},
            ),
        )
    )

    assert app._matchspec_query == "demo >=2"
    assert app._matchspec_records_by_package == {"demo": [record]}
    assert opened == ["demo"]
    assert focused == ["sidebar"]


def test_apply_matchspec_result_clears_package_search_filter(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    app._filter_mode = True
    app._search_query = "num"
    focused: list[str] = []

    monkeypatch.setattr(
        app,
        "_filter_packages",
        lambda: setattr(app, "_visible_package_names", list(app._all_package_names)),
    )
    monkeypatch.setattr(app, "_update_filter_indicator", lambda: None)
    monkeypatch.setattr(app, "_focus_sidebar", lambda: focused.append("sidebar"))

    asyncio.run(
        app._apply_matchspec_result(
            "python >=3.12",
            MatchSpecQueryResult(
                package_names=["python", "pypy"],
                records_by_package={
                    "python": [_make_repo_data_record(name="python")],
                    "pypy": [_make_repo_data_record(name="pypy")],
                },
            ),
        )
    )

    assert app._filter_mode is False
    assert app._search_query == ""
    assert app._visible_package_names == ["python", "pypy"]
    assert focused == ["sidebar"]


def test_apply_matchspec_result_keeps_packages_view_for_multiple_packages(
    monkeypatch,
) -> None:
    app = CondaMetadataTui()
    focused: list[str] = []
    opened: list[str] = []

    monkeypatch.setattr(
        app,
        "_filter_packages",
        lambda: setattr(app, "_visible_package_names", list(app._all_package_names)),
    )
    monkeypatch.setattr(app, "_update_filter_indicator", lambda: None)
    monkeypatch.setattr(app, "_focus_sidebar", lambda: focused.append("sidebar"))

    async def _fake_open_versions(package_name: str) -> None:
        opened.append(package_name)

    monkeypatch.setattr(app, "_open_versions", _fake_open_versions)

    asyncio.run(
        app._apply_matchspec_result(
            "py*",
            MatchSpecQueryResult(
                package_names=["python", "pypy"],
                records_by_package={
                    "python": [_make_repo_data_record(name="python")],
                    "pypy": [_make_repo_data_record(name="pypy")],
                },
            ),
        )
    )

    assert app._matchspec_query == "py*"
    assert app._all_package_names == ["python", "pypy"]
    assert app._mode == "packages"
    assert opened == []
    assert focused == ["sidebar"]


def test_apply_platform_selection_reapplies_active_matchspec(monkeypatch) -> None:
    app = CondaMetadataTui(default_platforms={Platform("linux-64")})
    app._available_platform_names = [Platform("linux-64"), Platform("noarch")]
    app._selected_platform_names = {Platform("linux-64")}
    app._draft_selected_platform_names = {Platform("linux-64"), Platform("noarch")}
    app._matchspec_query = "demo >=1"

    class _FakeStatus:
        def __init__(self) -> None:
            self.messages: list[object] = []

        def update(self, value: object) -> None:
            self.messages.append(value)

    class _FakeOptionList:
        def __init__(self) -> None:
            self.focused = False

        def focus(self) -> None:
            self.focused = True

    status = _FakeStatus()
    option_list = _FakeOptionList()
    reapplications: list[str] = []

    def _fake_query_one(selector: str, _widget_type: object = None) -> object:
        if selector == "#status":
            return status
        if selector == "#sidebar-list":
            return option_list
        raise AssertionError(selector)

    async def _fake_fetch_package_names_with_gateway() -> list[str]:
        return ["demo"]

    async def _fake_reapply_active_matchspec() -> None:
        reapplications.append(app._matchspec_query)

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(app, "_snapshot_channel_state", lambda: "snapshot")
    monkeypatch.setattr(app, "_clear_record_caches", lambda: None)
    monkeypatch.setattr(app, "_reset_preview_state", lambda: None)
    monkeypatch.setattr(app, "_update_platform_indicator", lambda: None)
    monkeypatch.setattr(app, "_update_filter_indicator", lambda: None)
    monkeypatch.setattr(
        app, "_fetch_package_names_with_gateway", _fake_fetch_package_names_with_gateway
    )
    monkeypatch.setattr(
        app, "_reapply_active_matchspec", _fake_reapply_active_matchspec
    )

    asyncio.run(app._apply_platform_selection())

    assert app._channel_package_names == ["demo"]
    assert reapplications == ["demo >=1"]
    assert option_list.focused is True


def test_apply_channel_selection_clears_active_matchspec(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._matchspec_query = "demo >=1"
    app._matchspec_records_by_package = {"demo": [_make_repo_data_record(name="demo")]}

    class _FakeOptionList:
        def __init__(self) -> None:
            self.disabled = False
            self.options: list[str] = []
            self.highlighted: int | None = None
            self.scroll_y = 0.0
            self.focused = False

        def clear_options(self) -> None:
            self.options.clear()

        def add_option(self, option: str) -> None:
            self.options.append(option)

        def focus(self) -> None:
            self.focused = True

    option_list = _FakeOptionList()
    notifications: list[str] = []

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeOptionList:
        assert selector == "#sidebar-list"
        return option_list

    async def _fake_load_packages() -> bool:
        return True

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(app, "_show_main_placeholder", lambda value: None)
    monkeypatch.setattr(app, "_update_filter_indicator", lambda: None)
    monkeypatch.setattr(app, "_load_packages", _fake_load_packages)
    monkeypatch.setattr(
        app, "notify", lambda message, **kwargs: notifications.append(message)
    )

    asyncio.run(app._apply_channel_selection("prefix.dev/conda-forge"))

    assert app._matchspec_query == ""
    assert app._matchspec_records_by_package == {}
    assert notifications == ["Switched to channel: prefix.dev/conda-forge"]


def test_footer_text_matches_redesigned_shortcuts() -> None:
    app = CondaMetadataTui()

    assert (
        app._footer_text()
        == "Search: / | Platform: p | Channel: c | MatchSpec: m | Help: ?"
    )


def test_footer_text_shows_download_hint_in_versions_mode() -> None:
    app = CondaMetadataTui()
    app._mode = "versions"

    assert (
        app._footer_text()
        == "Search: / | Platform: p | Channel: c | MatchSpec: m | Download: d | Help: ?"
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
        app._footer_text()
        == "Search: / | Platform: p | Channel: c | MatchSpec: m | Download: d | Help: ?"
    )


def test_on_paste_appends_sanitized_text_in_channel_edit_mode() -> None:
    app = CondaMetadataTui()
    app._channel_edit_mode = True
    app._channel_draft = "https://"
    app._update_filter_indicator = lambda: None  # type: ignore[method-assign]
    event = Paste("prefix.dev/conda-forge\n")

    app.on_paste(event)

    assert app._channel_draft == "https://prefix.dev/conda-forge"


def test_request_download_for_highlighted_entry_spawns_worker(monkeypatch) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )
    app._mode = "versions"
    app._selected_package = "demo"
    app._version_rows = [VersionRow(kind="entry", subdir="noarch", entry=entry)]

    class _FakeOptionList:
        highlighted = 0

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeOptionList:
        assert selector == "#sidebar-list"
        return _FakeOptionList()

    worker_calls: list[dict[str, object]] = []

    def _fake_run_worker(coro: object, **kwargs: object) -> None:
        worker_calls.append(kwargs)
        coro.close()  # type: ignore[attr-defined]

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(app, "run_worker", _fake_run_worker)
    app._request_download_for_highlighted_entry()

    assert worker_calls == [
        {
            "group": "version-download",
            "exclusive": True,
            "exit_on_error": False,
        }
    ]


def test_request_download_is_ignored_while_download_in_progress(monkeypatch) -> None:
    app = CondaMetadataTui()
    app._mode = "versions"
    app._download_in_progress = True
    app._selected_package = "demo"

    worker_calls: list[dict[str, object]] = []

    def _fake_run_worker(coro: object, **kwargs: object) -> None:
        del coro
        worker_calls.append(kwargs)

    def _fake_query_one(selector: str, _widget_type: object = None) -> object:
        del selector, _widget_type
        raise AssertionError("query_one should not be called while download is active")

    monkeypatch.setattr(app, "run_worker", _fake_run_worker)
    monkeypatch.setattr(app, "query_one", _fake_query_one)

    app._request_download_for_highlighted_entry()

    assert worker_calls == []


def test_handle_file_action_result_spawns_worker(monkeypatch) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )
    worker_calls: list[dict[str, object]] = []

    def _fake_run_worker(coro: object, **kwargs: object) -> None:
        worker_calls.append(kwargs)
        coro.close()  # type: ignore[attr-defined]

    monkeypatch.setattr(app, "run_worker", _fake_run_worker)

    app._handle_file_action_result("demo", entry, "info/about.json", "download")

    assert worker_calls == [
        {
            "group": "file-action",
            "exclusive": True,
            "exit_on_error": False,
        }
    ]


def test_download_selected_package_file_writes_relative_path(
    tmp_path, monkeypatch
) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )
    notifications: list[str] = []

    async def _fake_fetch(
        package_name: str, selected_entry: VersionEntry, file_path: str
    ) -> bytes:
        assert package_name == "demo"
        assert selected_entry == entry
        assert file_path == "site-packages/demo.py"
        return b"print('demo')\n"

    def _fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app, "_fetch_package_file_bytes", _fake_fetch)
    monkeypatch.setattr(app, "notify", _fake_notify)

    asyncio.run(
        app._download_selected_package_file("demo", entry, "site-packages/demo.py")
    )

    destination = (tmp_path / "site-packages" / "demo.py").resolve()
    assert destination.read_bytes() == b"print('demo')\n"
    assert notifications == [f"Downloaded file to {destination}"]


def test_preview_selected_package_file_uses_pager(monkeypatch) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )
    commands: list[list[str]] = []

    async def _fake_fetch(
        package_name: str, selected_entry: VersionEntry, file_path: str
    ) -> bytes:
        assert package_name == "demo"
        assert selected_entry == entry
        assert file_path == "info/about.json"
        return b'{"name": "demo"}\n'

    class _SuspendContext:
        def __enter__(self) -> None:
            return None

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb
            return None

    monkeypatch.setattr(app, "_fetch_package_file_bytes", _fake_fetch)
    monkeypatch.setattr(app, "suspend", lambda: _SuspendContext())
    monkeypatch.setattr("pixi_browse.tui.app._resolve_pager_command", lambda: ["less"])
    monkeypatch.setattr(
        "pixi_browse.tui.app.subprocess.run",
        lambda command, check: commands.append(command),
    )

    asyncio.run(app._preview_selected_package_file("demo", entry, "info/about.json"))

    assert commands
    assert commands[0][0] == "less"
    assert commands[0][-1].endswith(".json")


def test_copy_selected_package_file_to_clipboard(monkeypatch) -> None:
    app = CondaMetadataTui()
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )
    copied: list[str] = []
    notifications: list[str] = []

    async def _fake_fetch(
        package_name: str, selected_entry: VersionEntry, file_path: str
    ) -> bytes:
        assert package_name == "demo"
        assert selected_entry == entry
        assert file_path == "info/index.json"
        return b'{"subdir": "noarch"}\n'

    def _fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    monkeypatch.setattr(app, "_fetch_package_file_bytes", _fake_fetch)
    monkeypatch.setattr("pixi_browse.tui.app._copy_text_to_clipboard", copied.append)
    monkeypatch.setattr(app, "notify", _fake_notify)

    asyncio.run(
        app._copy_selected_package_file_to_clipboard("demo", entry, "info/index.json")
    )

    assert copied == ['{"subdir": "noarch"}\n']
    assert notifications == ["Copied info/index.json to clipboard"]


def test_download_selected_version_entry_downloads_to_cwd_and_notifies(
    tmp_path, monkeypatch
) -> None:
    source_file = tmp_path / "source-artifact.conda"
    source_file.write_bytes(b"artifact-bytes")

    app = CondaMetadataTui()
    app._mode = "versions"
    entry = VersionEntry(
        version=Version("1.2.3"),
        build="py313h123_0",
        build_number=0,
        subdir="noarch",
        file_name="demo-1.2.3-py313h123_0.conda",
    )

    class _FakeStyles:
        border_subtitle_align = "right"

    class _FakeMainPanel:
        def __init__(self) -> None:
            self.styles = _FakeStyles()
            self._border_subtitle: Text | str = ""
            self.subtitle_history: list[str] = []

        @property
        def border_subtitle(self) -> Text | str:
            return self._border_subtitle

        @border_subtitle.setter
        def border_subtitle(self, value: Text | str) -> None:
            self._border_subtitle = value
            if isinstance(value, Text):
                self.subtitle_history.append(value.plain)
            else:
                self.subtitle_history.append(value)

    class _FakeStatus:
        updates: list[Text | str]

        def __init__(self) -> None:
            self.updates = []

        def update(self, value: Text | str) -> None:
            self.updates.append(value)

    class _FakeFooter:
        updates: list[Text | str]

        def __init__(self) -> None:
            self.updates = []

        def update(self, value: Text | str) -> None:
            self.updates.append(value)

    main_panel = _FakeMainPanel()
    status = _FakeStatus()
    footer = _FakeFooter()
    notifications: list[str] = []
    captured_downloads: list[tuple[object, str, object]] = []

    def _fake_query_one(
        selector: str, _widget_type: object = None
    ) -> _FakeMainPanel | _FakeStatus | _FakeFooter:
        if selector == "#main-panel":
            return main_panel
        if selector == "#status":
            return status
        assert selector == "#footer"
        return footer

    async def _fake_get_record_for_version_entry(
        package_name: str, _entry: VersionEntry
    ) -> _RecordWithUrl:
        assert package_name == "demo"
        return _RecordWithUrl(url=source_file.resolve().as_uri())

    def _fake_notify(message: str, **kwargs: object) -> None:
        del kwargs
        notifications.append(message)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app, "query_one", _fake_query_one)
    monkeypatch.setattr(
        app, "_get_record_for_version_entry", _fake_get_record_for_version_entry
    )

    def _fake_download(
        client: object, url: str, destination: object
    ) -> Coroutine[object, object, str]:
        captured_downloads.append((client, url, destination))
        return asyncio.sleep(
            0,
            result=str(shutil.copyfile(source_file, str(destination))),
        )

    monkeypatch.setattr(
        "pixi_browse.tui.package_download_to_path",
        _fake_download,
    )
    monkeypatch.setattr(app, "notify", _fake_notify)

    asyncio.run(app._download_selected_version_entry("demo", entry))

    destination = (tmp_path / entry.file_name).resolve()
    assert captured_downloads == [
        (
            app._client,
            source_file.resolve().as_uri(),
            destination.with_name(f"{destination.name}.part"),
        )
    ]
    assert destination.read_bytes() == b"artifact-bytes"
    assert app._download_in_progress is False
    assert main_panel.subtitle_history == []
    assert status.updates[-1] == "0 entries across 0 platform."
    assert (
        f"Search: / | Platform: p | Channel: c | MatchSpec: m | Downloading {entry.file_name}... | Help: ?"
        in footer.updates
    )
    assert (
        "Search: / | Platform: p | Channel: c | MatchSpec: m | Download: d | Help: ?"
        in footer.updates
    )
    assert notifications == [f"Downloaded successfully to {destination}"]
