from click.utils import strip_ansi
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
        ) -> None:
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
