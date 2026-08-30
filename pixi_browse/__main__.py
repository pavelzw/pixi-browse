from __future__ import annotations

import typer
from rattler.exceptions import InvalidMatchSpecError, ParsePlatformError
from rattler.match_spec import MatchSpec
from rattler.platform import Platform

from pixi_browse import __version__
from pixi_browse.artifacts import InvalidArtifactSourceError, parse_artifact_source
from pixi_browse.models import ArtifactSource, VersionEntry, VersionRow
from pixi_browse.tui import CondaMetadataTui

__all__ = [
    "CondaMetadataTui",
    "VersionEntry",
    "VersionRow",
    "cli",
    "run",
]


def _version_callback(value: bool) -> None:
    if not value:
        return
    typer.echo(f"pixi-browse {__version__}")
    raise typer.Exit()


cli = typer.Typer(
    add_completion=False,
    help="Browse conda package metadata in a Textual TUI.",
)


@cli.callback(invoke_without_command=True)
def run(
    ctx: typer.Context,
    channel: str = typer.Option(
        "conda-forge",
        "--channel",
        "-c",
        help="Default channel loaded at startup.",
    ),
    platform: list[str] | None = typer.Option(
        None,
        "--platform",
        "-p",
        help="Default platforms. Repeat the flag to pass multiple platforms.",
    ),
    matchspec: str | None = typer.Option(
        None,
        "--matchspec",
        "-m",
        help="Apply a MatchSpec query at startup.",
    ),
    _version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    requested_platforms: list[Platform] | None = None
    requested_matchspec: MatchSpec | None = None
    if platform is not None:
        try:
            requested_platforms = [
                Platform(platform_name) for platform_name in platform
            ]
        except ParsePlatformError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    if matchspec is not None and matchspec.strip():
        try:
            requested_matchspec = MatchSpec(matchspec.strip(), exact_names_only=False)
        except InvalidMatchSpecError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    CondaMetadataTui(
        default_channel=channel,
        default_platforms=requested_platforms,
        default_matchspec=requested_matchspec,
    ).run()


def _parse_source(value: str, *, argument_name: str) -> ArtifactSource:
    try:
        return parse_artifact_source(value)
    except InvalidArtifactSourceError as exc:
        raise typer.BadParameter(str(exc), param_hint=argument_name) from exc


@cli.command("inspect")
def inspect_artifact(
    source: str = typer.Argument(
        ...,
        help="Local path or HTTP(S) URL to a .conda or .tar.bz2 artifact.",
    ),
) -> None:
    artifact_source = _parse_source(source, argument_name="SOURCE")
    CondaMetadataTui(artifact_sources=[artifact_source]).run()


@cli.command("compare")
def compare_artifacts(
    left: str = typer.Argument(
        ...,
        help="Local path or HTTP(S) URL for the left artifact.",
    ),
    right: str = typer.Argument(
        ...,
        help="Local path or HTTP(S) URL for the right artifact.",
    ),
) -> None:
    left_source = _parse_source(left, argument_name="LEFT")
    right_source = _parse_source(right, argument_name="RIGHT")
    CondaMetadataTui(
        artifact_sources=[left_source, right_source],
        compare_artifacts=True,
    ).run()


if __name__ == "__main__":
    cli()
