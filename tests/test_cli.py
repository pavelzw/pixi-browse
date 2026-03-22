from click.utils import strip_ansi
from rattler.match_spec import MatchSpec
from rattler.platform import Platform
from typer.testing import CliRunner

import pixi_browse.__main__ as entrypoint
from pixi_browse import __version__


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
