import re

import pytest
from rattler.match_spec import MatchSpec
from rattler.platform import Platform
from typer.testing import CliRunner

import pixi_browse.__main__ as entrypoint
from pixi_browse import __version__

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


def test_build_app_passes_channel_and_platforms() -> None:
    app = entrypoint.build_app(
        channels=["https://prefix.dev/conda-forge"],
        platforms=["linux-64", "noarch", "osx-arm64"],
        matchspec=None,
    )

    assert app._channel_names == ["https://prefix.dev/conda-forge"]
    assert app._selected_platform_names == {
        Platform("linux-64"),
        Platform("noarch"),
        Platform("osx-arm64"),
    }
    assert app._startup_matchspec is None


def test_build_app_keeps_channel_order_and_drops_repeats() -> None:
    app = entrypoint.build_app(
        channels=["bioconda", " conda-forge ", "bioconda", ""],
        platforms=None,
        matchspec=None,
    )

    assert app._channel_names == ["bioconda", "conda-forge"]


def test_cli_defaults_the_channel_option_to_conda_forge() -> None:
    runner = CliRunner()

    result = runner.invoke(entrypoint.cli, ["--help"])
    output = strip_ansi(result.output)

    assert result.exit_code == 0
    assert re.search(r"default:\s+conda-forge", output)


def test_cli_exits_without_any_channel() -> None:
    runner = CliRunner()

    result = runner.invoke(entrypoint.cli, ["-c", "  "])

    assert result.exit_code == 1
    assert "At least one channel is required." in result.output


def test_build_app_passes_matchspec() -> None:
    app = entrypoint.build_app(
        channels=["conda-forge"], platforms=None, matchspec=" numpy >=2 "
    )

    assert app._channel_names == ["conda-forge"]
    assert app._selected_platform_names == set()
    assert isinstance(app._startup_matchspec, MatchSpec)
    assert str(app._startup_matchspec) == "numpy >=2"


@pytest.mark.parametrize("matchspec", [None, "", "   "])
def test_build_app_ignores_blank_matchspec(matchspec: str | None) -> None:
    app = entrypoint.build_app(
        channels=["conda-forge"], platforms=None, matchspec=matchspec
    )

    assert app._startup_matchspec is None


def test_cli_exits_for_invalid_platform() -> None:
    runner = CliRunner()

    result = runner.invoke(entrypoint.cli, ["-p", "linux-64", "-p", "bad-platform"])

    assert result.exit_code == 1
    assert "bad-platform" in result.output
    assert "not a known platform" in result.output


def test_cli_exits_for_invalid_matchspec() -> None:
    runner = CliRunner()

    result = runner.invoke(entrypoint.cli, ["-m", "numpy["])

    assert result.exit_code == 1
    assert result.output.strip()
