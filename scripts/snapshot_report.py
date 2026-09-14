"""Build a Textual snapshot report from two Git revisions."""

from __future__ import annotations

import argparse
import ast
import html as html_escaping
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from pytest_textual_snapshot.plugin import (  # type: ignore[import-untyped]
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
# Git's status for a rename whose content is byte for byte the same.
RENAMED_UNCHANGED = "R100"

# Anchors in pytest-textual-snapshot's report template that we extend: with a
# switch flipping every snapshot into its diff view at once, and with a button
# per snapshot that pops up the source code of its test.
SUMMARY_ANCHOR = '<div class="w-100 d-flex gap-1 justify-content-end mb-1 mt-2">'
BODY_ANCHOR = "</body>"
CARD_HEADER_ANCHOR = '<div class="card-header d-flex justify-content-between">'
CARD_BODY_ANCHOR = '<div class="card-body">'
ENVIRONMENT_MODAL_ANCHOR = '<div class="modal modal-lg fade" id="environmentModal'

TOGGLE_ALL_SWITCH = """\
<div class="form-check form-switch me-3">
    <input class="form-check-input" type="checkbox" role="switch" id="toggle-all-diffs"
           onchange="toggleAllOverlays(this.checked)">
    <label class="form-check-label text-muted" for="toggle-all-diffs">
        Show all differences
    </label>
</div>
"""

TOGGLE_ALL_SCRIPT = """\
<script type="application/javascript">
    function toggleAllOverlays(show) {
        for (const overlay of document.querySelectorAll(".diff-wrapper-actual")) {
            overlay.hidden = !show
        }
        // Keep the per-snapshot switches in step, so that flipping one of them
        // afterwards still hides the overlay it belongs to.
        for (const switch_ of document.querySelectorAll('.card-header input[role="switch"]')) {
            switch_.checked = show
        }
    }
</script>
"""

TEST_SOURCE_STYLE = """\
<style>
    .test-source {
        background-color: #0c0c0c;
        color: #f8f9fa;
        font-size: 0.875rem;
        line-height: 1.5;
    }
    .test-source .line-number {
        display: inline-block;
        width: 3.5em;
        margin-right: 1.5em;
        text-align: right;
        color: #6c757d;
        user-select: none;
    }
</style>
"""

# `ms-auto` takes the free space of the header's `justify-content-between`, so
# the button sits right before the "Show difference" switch, or at the right
# edge for a snapshot that has no history and therefore no switch.
TEST_SOURCE_BUTTON = """\
<button type="button" class="btn btn-sm btn-outline-light text-nowrap ms-auto me-3 align-self-start"
        data-bs-toggle="modal" data-bs-target="#testSourceModal{index}">
    View test source
</button>
"""

TEST_SOURCE_MODAL = """\
<div class="modal modal-xl fade" id="testSourceModal{index}" tabindex="-1"
     aria-labelledby="testSourceModalLabel{index}" aria-hidden="true">
    <div class="modal-dialog modal-dialog-scrollable">
        <div class="modal-content bg-dark text-white">
            <div class="modal-header">
                <h5 class="modal-title" id="testSourceModalLabel{index}">
                    Source of <span class="font-monospace">{test_name}</span>
                    <span class="text-muted font-monospace small ps-2">{location}</span>
                </h5>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"
                        aria-label="Close"></button>
            </div>
            <div class="modal-body p-0">
<pre class="test-source p-3 mb-0"><code>{source}</code></pre>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">
                    Close
                </button>
            </div>
        </div>
    </div>
</div>
"""


@dataclass(frozen=True)
class SnapshotChange:
    """An SVG snapshot path before and after a Git change."""

    status: str
    before_path: PurePosixPath | None
    after_path: PurePosixPath | None


@dataclass(frozen=True)
class TestSource:
    """The committed source code of a snapshot's test function."""

    module_path: PurePosixPath
    line_number: int
    """The first line of the function, decorators included."""
    source: str
    docstring: str

    @property
    def location(self) -> str:
        """Where the function starts, as ``<module path>:<line>``."""
        return f"{self.module_path}:{self.line_number}"


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


def read_committed_file(revision: str, path: PurePosixPath) -> str | None:
    """The content of ``path`` in ``revision``, or None if it has no such file."""
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


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
        if status == RENAMED_UNCHANGED:
            # A snapshot that only moved renders the same screen on both sides
            # of the report, so listing it would be noise.
            continue
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


def test_name_and_palette(snapshot_path: PurePosixPath) -> tuple[str, str]:
    """Split a snapshot file name into its test name and its palette.

    Snapshots are named ``<test>.<palette>.svg``; see `tests/helpers.py`.
    """
    test_name, _, palette = snapshot_path.stem.rpartition(".")
    return test_name, palette


def report_test_name(snapshot_path: PurePosixPath) -> str:
    """The report label of a snapshot: its test name, plus its palette."""
    test_name, palette = test_name_and_palette(snapshot_path)
    return f"{test_name} ({palette})"


def test_source(revision: str, snapshot_path: PurePosixPath) -> TestSource | None:
    """Read a snapshot's test function from its committed Python module.

    Returns None when the revision has no such module or the module no
    longer defines the test, so a stale snapshot still lands in the report,
    just without a source popup.
    """
    parts = snapshot_path.parts
    try:
        snapshot_directory_index = parts.index("__snapshots__")
    except ValueError:
        return None

    # A snapshot sits in a directory named after its test module.
    module_name = snapshot_path.parent.name
    module_path = PurePosixPath(*parts[:snapshot_directory_index], f"{module_name}.py")
    module_source = read_committed_file(revision, module_path)
    if module_source is None:
        return None
    module = ast.parse(module_source)
    test_name = test_name_and_palette(snapshot_path)[0].partition("[")[0]

    for node in ast.walk(module):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name == test_name:
                first_line = min(
                    (decorator.lineno for decorator in node.decorator_list),
                    default=node.lineno,
                )
                last_line = node.end_lineno or node.lineno
                lines = module_source.splitlines()[first_line - 1 : last_line]
                return TestSource(
                    module_path=module_path,
                    line_number=first_line,
                    source="\n".join(lines),
                    docstring=ast.get_docstring(node, clean=True) or "",
                )
    return None


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
) -> list[tuple[SvgSnapshotDiff, TestSource | None]]:
    """Convert Git changes into pytest-textual-snapshot's report model.

    Every diff comes with the source of its test: from ``head`` for a snapshot
    that still exists there, from ``base`` for a deleted one.
    """
    console = Console(legacy_windows=False, force_terminal=True)
    app = PseudoApp(PseudoConsole(console.legacy_windows, console.size))
    diffs: list[tuple[SvgSnapshotDiff, TestSource | None]] = []

    for index, change in enumerate(changes):
        before = read_snapshot(base, change.before_path)
        after = read_snapshot(head, change.after_path)
        display_path = change.after_path or change.before_path
        assert display_path is not None

        actual = after or missing_snapshot_svg(f"Not present in {head}")
        snapshot = before or missing_snapshot_svg(f"Not present in {base}")
        source = (
            test_source(head, change.after_path)
            if change.after_path is not None
            else test_source(base, display_path)
        )

        diff = SvgSnapshotDiff(
            snapshot=individualize_svg(snapshot, f"base-{index}"),
            actual=individualize_svg(actual, f"head-{index}"),
            test_name=report_test_name(display_path),
            path=Path(display_path),
            line_number=1,
            app=app,
            environment={},
            docstring=source.docstring if source is not None else "",
            app_path=None,
            snapshot_exists=before is not None,
        )
        diffs.append((diff, source))

    return diffs


def template_changed(anchor: str, report: Path) -> SystemExit:
    """The error for a report anchor that pytest-textual-snapshot moved."""
    return SystemExit(
        f"Could not find {anchor!r} in {report}; the report template of "
        "pytest-textual-snapshot changed."
    )


def find_anchor(html: str, anchor: str, report: Path, start: int = 0) -> int:
    """The position of ``anchor`` in ``html`` at or after ``start``."""
    position = html.find(anchor, start)
    if position == -1:
        raise template_changed(anchor, report)
    return position


def add_toggle_all_switch(report: Path) -> None:
    """Add a switch that shows the diff view of every snapshot at once."""
    html = report.read_text()
    for anchor in (SUMMARY_ANCHOR, BODY_ANCHOR):
        find_anchor(html, anchor, report)
    html = html.replace(SUMMARY_ANCHOR, SUMMARY_ANCHOR + TOGGLE_ALL_SWITCH, 1)
    html = html.replace(BODY_ANCHOR, TOGGLE_ALL_SCRIPT + BODY_ANCHOR, 1)
    report.write_text(html)


def highlighted_source(source: TestSource) -> str:
    """The test source as HTML, one numbered line per source line."""
    return "\n".join(
        f'<span class="line-number">{number}</span>{html_escaping.escape(line)}'
        for number, line in enumerate(source.source.splitlines(), source.line_number)
    )


def add_test_source_popups(
    report: Path, sources: list[tuple[str, TestSource | None]]
) -> None:
    """Add a button per snapshot that pops up the source code of its test.

    ``sources`` lists the test name and source of every snapshot in the order
    the report shows them, which is how the popups find their cards.
    """
    html = report.read_text()
    find_anchor(html, BODY_ANCHOR, report)
    html = html.replace(BODY_ANCHOR, TEST_SOURCE_STYLE + BODY_ANCHOR, 1)

    position = 0
    for index, (test_name, source) in enumerate(sources):
        header = find_anchor(html, CARD_HEADER_ANCHOR, report, position)
        body = find_anchor(html, CARD_BODY_ANCHOR, report, header)
        modal = find_anchor(html, f'{ENVIRONMENT_MODAL_ANCHOR}{index}"', report, body)
        if source is None:
            position = modal
            continue

        # The header closes right before the card body opens.
        header_end = html.rfind("</div>", header, body)
        if header_end == -1:
            raise template_changed("</div>", report)
        button = TEST_SOURCE_BUTTON.format(index=index)
        source_modal = TEST_SOURCE_MODAL.format(
            index=index,
            test_name=html_escaping.escape(test_name),
            location=html_escaping.escape(source.location),
            source=highlighted_source(source),
        )
        html = (
            html[:header_end]
            + button
            + html[header_end:modal]
            + source_modal
            + html[modal:]
        )
        position = modal + len(button) + len(source_modal)

    report.write_text(html)


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

    diffs_with_sources = build_diffs(changes, args.base, args.head)
    diffs = [diff for diff, _ in diffs_with_sources]
    session = cast("Session", ReportSession(args.output))
    num_snapshots_passing = unchanged_svg_snapshot_count(changes, args.head)
    save_svg_diffs(diffs, session, num_snapshots_passing=num_snapshots_passing)
    add_toggle_all_switch(args.output)
    # `save_svg_diffs` lists the snapshots sorted by test name; the sources
    # must follow the same order to end up on the right cards.
    sources = [
        (diff.test_name, source)
        for diff, source in sorted(
            diffs_with_sources, key=lambda pair: pair[0].test_name
        )
    ]
    add_test_source_popups(args.output, sources)
    print(f"Wrote {args.output} with {len(diffs)} changed snapshot(s).")


if __name__ == "__main__":
    main()
