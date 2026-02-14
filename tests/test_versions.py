import asyncio
import io
from dataclasses import dataclass

from rattler.platform import Platform
from rattler.version import Version
from rich.text import Text
from textual.events import Paste

from pixi_browse.__main__ import CondaMetadataTui, VersionEntry, VersionRow


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


def test_build_version_entries_preserves_artifacts_per_build() -> None:
    app = CondaMetadataTui()
    version = Version("1.2.3")
    records = [
        _Record(
            version=version,
            build="py313h123_0",
            build_number=0,
            subdir="noarch",
            file_name="demo-1.2.3-py313h123_0.conda",
        ),
        _Record(
            version=version,
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


def test_ensure_available_platforms_removes_unavailable_selected_platforms() -> None:
    app = CondaMetadataTui(default_platforms={Platform("linux-64"), Platform("osx-64")})
    app._available_platform_names = [Platform("linux-64"), Platform("noarch")]

    asyncio.run(app._ensure_available_platforms())

    assert app._selected_platform_names == {Platform("linux-64")}


def test_ensure_available_platforms_falls_back_to_default_when_needed() -> None:
    app = CondaMetadataTui(default_platforms={Platform("osx-64")})
    app._available_platform_names = [Platform("linux-64"), Platform("noarch")]

    asyncio.run(app._ensure_available_platforms())

    assert app._selected_platform_names == {Platform("noarch")}


def test_rerender_visible_version_preview_invalidates_cache_on_resize(
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
    app._version_details_cache = {
        ("demo", "1.2.3", "py313h123_0", 0, "noarch", "old"): "cached"
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

    assert app._version_details_cache == {}
    assert preview_calls == [("demo", entry)]


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
    updates: list[str] = []

    class _FakeStatus:
        def update(self, value: str) -> None:
            updates.append(value)

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeStatus:
        assert selector == "#status"
        return _FakeStatus()

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    app._update_versions_status()

    assert len(updates) == 1
    assert updates == [
        "1 entries across 1 platform(s). Enter toggles section.",
    ]


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

    main_panel = _FakeMainPanel()

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeMainPanel:
        assert selector == "#main-panel"
        return main_panel

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    app._update_download_indicator()

    assert main_panel.styles.border_subtitle_align == "left"
    assert isinstance(main_panel.border_title, Text)
    assert main_panel.border_title.plain == "channel conda-forge"
    assert isinstance(main_panel.border_subtitle, Text)
    assert main_panel.border_subtitle.plain == "download"
    assert any(
        str(span.style) == "bold red"
        and main_panel.border_subtitle.plain[span.start : span.end] == "d"
        for span in main_panel.border_subtitle.spans
    )


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

    main_panel = _FakeMainPanel()

    def _fake_query_one(selector: str, _widget_type: object = None) -> _FakeMainPanel:
        assert selector == "#main-panel"
        return main_panel

    monkeypatch.setattr(app, "query_one", _fake_query_one)
    app._update_download_indicator()

    assert isinstance(main_panel.border_title, Text)
    assert main_panel.border_title.plain == "channel conda-forge"
    assert main_panel.styles.border_subtitle_align == "left"
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


def test_action_channel_key_c_starts_channel_edit_mode() -> None:
    app = CondaMetadataTui()
    app._mode = "packages"
    app._filter_mode = False
    app._channel_name = "custom-channel"
    app._update_filter_indicator = lambda: None  # type: ignore[method-assign]

    app.action_channel_key_c()

    assert app._channel_edit_mode is True
    assert app._channel_draft == ""


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


def test_channel_indicator_text_shows_edit_draft() -> None:
    app = CondaMetadataTui()
    app._channel_edit_mode = True
    app._channel_draft = "prefix.dev/conda-forge"

    indicator = app._channel_indicator_text()

    assert indicator.plain == "channel prefix.dev/conda-forge_"


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


def test_download_url_to_path_uses_timeout(tmp_path, monkeypatch) -> None:
    app = CondaMetadataTui()
    destination = tmp_path / "artifact.conda"
    captured: dict[str, object] = {}

    def _fake_urlopen(url: str, timeout: float) -> io.BytesIO:
        captured["url"] = url
        captured["timeout"] = timeout
        return io.BytesIO(b"artifact-bytes")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    app._download_url_to_path(
        "https://example.invalid/artifact.conda",
        destination,
        timeout_seconds=12.5,
    )

    assert captured == {
        "url": "https://example.invalid/artifact.conda",
        "timeout": 12.5,
    }
    assert destination.read_bytes() == b"artifact-bytes"


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
        updates: list[str]

        def __init__(self) -> None:
            self.updates = []

        def update(self, value: str) -> None:
            self.updates.append(value)

    main_panel = _FakeMainPanel()
    status = _FakeStatus()
    notifications: list[str] = []

    def _fake_query_one(
        selector: str, _widget_type: object = None
    ) -> _FakeMainPanel | _FakeStatus:
        if selector == "#main-panel":
            return main_panel
        assert selector == "#status"
        return status

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
    monkeypatch.setattr(app, "notify", _fake_notify)

    asyncio.run(app._download_selected_version_entry("demo", entry))

    destination = (tmp_path / entry.file_name).resolve()
    assert destination.read_bytes() == b"artifact-bytes"
    assert app._download_in_progress is False
    assert f"Downloading {entry.file_name}..." in main_panel.subtitle_history
    assert "download" in main_panel.subtitle_history
    assert notifications == [f"Downloaded successfully to {destination}"]
