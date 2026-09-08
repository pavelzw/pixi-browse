from __future__ import annotations

import typer
from rattler.exceptions import InvalidMatchSpecError, ParsePlatformError
from rattler.match_spec import MatchSpec
from rattler.platform import Platform

from pixi_browse import __version__
from pixi_browse.models import VersionEntry, VersionRow
from pixi_browse.repodata import DEFAULT_CHANNEL, normalize_channel_names
from pixi_browse.tui import CondaMetadataTui

__all__ = [
    "CondaMetadataTui",
    "VersionEntry",
    "VersionRow",
    "build_app",
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
    channel: list[str] = typer.Option(
        [DEFAULT_CHANNEL],
        "--channel",
        "-c",
        help="Channels loaded at startup. Repeat the flag to pass multiple channels.",
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
    build_app(channels=channel, platforms=platform, matchspec=matchspec).run()


def build_app(
    *,
    channels: list[str],
    platforms: list[str] | None,
    matchspec: str | None,
) -> CondaMetadataTui:
    """Validate the command line options and build the (not yet running) app.

    Exits with status 1 on an unknown platform, an invalid MatchSpec, or when
    no channel is left after dropping blank and repeated ones.
    """
    channel_names = normalize_channel_names(channels)
    if not channel_names:
        typer.echo("At least one channel is required.", err=True)
        raise typer.Exit(code=1)
    requested_platforms: list[Platform] | None = None
    requested_matchspec: MatchSpec | None = None
    if platforms is not None:
        try:
            requested_platforms = [
                Platform(platform_name) for platform_name in platforms
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

    return CondaMetadataTui(
        default_channels=channel_names,
        default_platforms=requested_platforms,
        default_matchspec=requested_matchspec,
    )


if __name__ == "__main__":
    cli()
