"""Build a Textual snapshot report from two Git revisions."""

from __future__ import annotations

import argparse
import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from pytest_textual_snapshot import (  # type: ignore[import-untyped]
    PseudoApp,
    PseudoConsole,
    SvgSnapshotDiff,
    individualize_svg,
    save_svg_diffs,
)
from rich.console import Console

if TYPE_CHECKING:
    from _pytest.main import Session


SNAPSHOT_DIRECTORY = PurePosixPath("tests/__snapshots__")


@dataclass(frozen=True)
class SnapshotChange:
    """An SVG snapshot path before and after a Git change."""

    status: str
    before_path: PurePosixPath | None
    after_path: PurePosixPath | None


class ReportConfig:
    """The part of pytest's config used by ``save_svg_diffs``."""

    def __init__(self, output: Path) -> None:
        self.output = output

    def getoption(self, option: str) -> str:
        if option != "--snapshot-report":
            raise ValueError(f"Unexpected pytest option: {option}")
        return str(self.output)


class ReportSession:
    """The part of a pytest session used by ``save_svg_diffs``."""

    def __init__(self, output: Path) -> None:
        self.config = ReportConfig(output)


def run_git(*arguments: str) -> str:
    """Run Git and return its standard output with a concise failure message."""
    try:
        result = subprocess.run(
            ["git", *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip()
        raise SystemExit(message) from error
    return result.stdout


def validate_revision(revision: str) -> None:
    """Fail early when a requested Git revision is unavailable."""
    run_git("rev-parse", "--verify", f"{revision}^{{commit}}")


def changed_svg_snapshots(base: str, head: str) -> list[SnapshotChange]:
    """Return SVG snapshot changes between two Git trees."""
    output = run_git(
        "diff",
        "--name-status",
        "--find-renames",
        "-z",
        base,
        head,
        "--",
        str(SNAPSHOT_DIRECTORY),
    )
    fields = output.rstrip("\0").split("\0") if output else []
    changes: list[SnapshotChange] = []
    index = 0

    while index < len(fields):
        status = fields[index]
        index += 1
        change_type = status[0]
        before_path: PurePosixPath | None
        after_path: PurePosixPath | None

        if change_type in {"R", "C"}:
            before_path = PurePosixPath(fields[index])
            after_path = PurePosixPath(fields[index + 1])
            index += 2
        else:
            path = PurePosixPath(fields[index])
            index += 1
            before_path = None if change_type == "A" else path
            after_path = None if change_type == "D" else path

        paths = (before_path, after_path)
        if any(path is not None and path.suffix == ".svg" for path in paths):
            changes.append(SnapshotChange(status, before_path, after_path))

    return changes


def unchanged_svg_snapshot_count(changes: list[SnapshotChange], head: str) -> int:
    """Count SVG snapshots in the head tree that are absent from the changes."""
    output = run_git(
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        head,
        "--",
        str(SNAPSHOT_DIRECTORY),
    )
    head_paths = {
        PurePosixPath(path)
        for path in output.rstrip("\0").split("\0")
        if path and PurePosixPath(path).suffix == ".svg"
    }
    changed_head_paths = {
        change.after_path
        for change in changes
        if change.after_path is not None and change.after_path.suffix == ".svg"
    }
    return len(head_paths - changed_head_paths)


def read_snapshot(revision: str, path: PurePosixPath | None) -> str | None:
    """Read an SVG snapshot from a revision, if that side of the change exists."""
    if path is None or path.suffix != ".svg":
        return None
    return run_git("show", f"{revision}:{path}")


def test_docstring(revision: str, snapshot_path: PurePosixPath) -> str:
    """Read a snapshot's test docstring from its committed Python module."""
    parts = snapshot_path.parts
    try:
        snapshot_directory_index = parts.index("__snapshots__")
        module_name = parts[snapshot_directory_index + 1]
    except (ValueError, IndexError):
        return ""

    module_path = PurePosixPath(*parts[:snapshot_directory_index], f"{module_name}.py")
    module = ast.parse(run_git("show", f"{revision}:{module_path}"))
    test_name = snapshot_path.stem.partition("[")[0]

    for node in ast.walk(module):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name == test_name:
                return ast.get_docstring(node, clean=True) or ""
    return ""


def missing_snapshot_svg(label: str) -> str:
    """Create a terminal-sized placeholder for an absent side of a change."""
    return f"""\
<svg class="rich-terminal" viewBox="0 0 1482 1026" xmlns="http://www.w3.org/2000/svg">
  <rect width="1482" height="1026" fill="#0c0c0c" />
  <text x="741" y="513" fill="#adb5bd" font-family="monospace" font-size="32"
        text-anchor="middle">{label}</text>
</svg>
"""


def build_diffs(
    changes: list[SnapshotChange], base: str, head: str
) -> list[SvgSnapshotDiff]:
    """Convert Git changes into pytest-textual-snapshot's report model."""
    console = Console(legacy_windows=False, force_terminal=True)
    app = PseudoApp(PseudoConsole(console.legacy_windows, console.size))
    diffs: list[SvgSnapshotDiff] = []

    for index, change in enumerate(changes):
        before = read_snapshot(base, change.before_path)
        after = read_snapshot(head, change.after_path)
        display_path = change.after_path or change.before_path
        assert display_path is not None

        actual = after or missing_snapshot_svg(f"Not present in {head}")
        snapshot = before or missing_snapshot_svg(f"Not present in {base}")
        docstring = (
            test_docstring(head, change.after_path)
            if change.after_path is not None
            else ""
        )

        diffs.append(
            SvgSnapshotDiff(
                snapshot=individualize_svg(snapshot, f"base-{index}"),
                actual=individualize_svg(actual, f"head-{index}"),
                test_name=display_path.stem,
                path=Path(display_path),
                line_number=1,
                app=app,
                environment={},
                docstring=docstring,
                app_path=None,
                snapshot_exists=before is not None,
            )
        )

    return diffs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate snapshot_report.html by comparing Git revisions."
    )
    parser.add_argument("--base", default="main", help="Historical revision")
    parser.add_argument("--head", default="HEAD", help="Current revision")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("snapshot_report.html"),
        help="Destination HTML file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_revision(args.base)
    validate_revision(args.head)
    changes = changed_svg_snapshots(args.base, args.head)
    if not changes:
        print(
            f"No SVG snapshots changed between {args.base} and {args.head} "
            f"under {SNAPSHOT_DIRECTORY}."
        )
        return

    diffs = build_diffs(changes, args.base, args.head)
    session = cast("Session", ReportSession(args.output))
    num_snapshots_passing = unchanged_svg_snapshot_count(changes, args.head)
    save_svg_diffs(diffs, session, num_snapshots_passing=num_snapshots_passing)
    print(f"Wrote {args.output} with {len(diffs)} changed snapshot(s).")


if __name__ == "__main__":
    main()
