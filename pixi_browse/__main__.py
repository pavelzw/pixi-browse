from __future__ import annotations

import typer
from rattler.exceptions import InvalidMatchSpecError, ParsePlatformError
from rattler.match_spec import MatchSpec
from rattler.platform import Platform

from pixi_browse import __version__
from pixi_browse.models import VersionEntry, VersionRow
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


if __name__ == "__main__":
    cli()
