import re
from pathlib import Path

from rattler.match_spec import MatchSpec
from rattler.platform import Platform
from typer.testing import CliRunner

import pixi_browse.__main__ as entrypoint
from pixi_browse import __version__
from pixi_browse.models import LocalArtifactSource, RemoteArtifactSource

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE.sub("", value)


def test_help_includes_expected_options() -> None:
    runner = CliRunner()

    result = runner.invoke(entrypoint.cli, ["--help"])
    output = strip_ansi(result.output)

    assert result.exit_code == 0
    assert "--channel" in output
    assert "--matchspec" in output
    assert "--platform" in output
    assert "--version" in output


def test_version_flag_prints_version_and_exits() -> None:
    runner = CliRunner()

    result = runner.invoke(entrypoint.cli, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"pixi-browse {__version__}"


def test_cli_passes_channel_and_platforms(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    class _FakeTui:
        def __init__(
            self,
            *,
            default_channel: str = "conda-forge",
            default_platforms: list[Platform] | None = None,
            default_matchspec: MatchSpec | None = None,
        ) -> None:
            del default_matchspec
            captured["channel"] = default_channel
            captured["platforms"] = {
                str(platform) for platform in (default_platforms or [])
            }

        def run(self) -> None:
            captured["run_called"] = True

    monkeypatch.setattr(entrypoint, "CondaMetadataTui", _FakeTui)

    result = runner.invoke(
        entrypoint.cli,
        [
            "-c",
            "prefix.dev/conda-forge",
            "-p",
            "linux-64",
            "-p",
            "noarch",
            "-p",
            "osx-arm64",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "channel": "prefix.dev/conda-forge",
        "platforms": {"linux-64", "noarch", "osx-arm64"},
        "run_called": True,
    }


def test_cli_passes_matchspec(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    class _FakeTui:
        def __init__(
            self,
            *,
            default_channel: str = "conda-forge",
            default_platforms: list[Platform] | None = None,
            default_matchspec: MatchSpec | None = None,
        ) -> None:
            del default_platforms
            captured["channel"] = default_channel
            captured["matchspec"] = (
                None if default_matchspec is None else str(default_matchspec)
            )

        def run(self) -> None:
            captured["run_called"] = True

    monkeypatch.setattr(entrypoint, "CondaMetadataTui", _FakeTui)

    result = runner.invoke(
        entrypoint.cli,
        ["-c", "conda-forge", "-m", "numpy >=2"],
    )

    assert result.exit_code == 0
    assert captured == {
        "channel": "conda-forge",
        "matchspec": "numpy >=2",
        "run_called": True,
    }


def test_cli_exits_for_invalid_platform(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    class _FakeTui:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

        def run(self) -> None:
            captured["run_called"] = True

    monkeypatch.setattr(entrypoint, "CondaMetadataTui", _FakeTui)

    result = runner.invoke(entrypoint.cli, ["-p", "linux-64", "-p", "bad-platform"])

    assert result.exit_code == 1
    assert "bad-platform" in result.output
    assert "not a known platform" in result.output
    assert captured == {}


def test_cli_exits_for_invalid_matchspec(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    class _FakeTui:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

        def run(self) -> None:
            captured["run_called"] = True

    monkeypatch.setattr(entrypoint, "CondaMetadataTui", _FakeTui)

    result = runner.invoke(entrypoint.cli, ["-m", "numpy["])

    assert result.exit_code == 1
    assert result.output.strip()
    assert captured == {}


def test_inspect_command_passes_local_artifact_source(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    artifact_path = tmp_path / "demo.conda"
    artifact_path.write_bytes(b"package")
    captured: dict[str, object] = {}

    class _FakeTui:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self) -> None:
            captured["run_called"] = True

    monkeypatch.setattr(entrypoint, "CondaMetadataTui", _FakeTui)

    result = runner.invoke(entrypoint.cli, ["inspect", str(artifact_path)])

    assert result.exit_code == 0
    assert captured == {
        "artifact_sources": [LocalArtifactSource(Path(artifact_path).resolve())],
        "run_called": True,
    }


def test_compare_command_preserves_remote_artifact_order(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    class _FakeTui:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self) -> None:
            captured["run_called"] = True

    monkeypatch.setattr(entrypoint, "CondaMetadataTui", _FakeTui)

    result = runner.invoke(
        entrypoint.cli,
        [
            "compare",
            "https://example.com/old.conda",
            "https://example.com/new.conda?token=secret",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "artifact_sources": [
            RemoteArtifactSource("https://example.com/old.conda"),
            RemoteArtifactSource("https://example.com/new.conda?token=secret"),
        ],
        "compare_artifacts": True,
        "run_called": True,
    }


def test_inspect_command_rejects_missing_local_artifact(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(entrypoint.cli, ["inspect", str(tmp_path / "missing.conda")])

    assert result.exit_code == 2
    assert "Artifact does not exist" in strip_ansi(result.output)
