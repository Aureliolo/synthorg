#!/usr/bin/env python3
"""Module-depth budget gate.

Walks ``src/synthorg/`` and verifies every Python file's nesting depth
is within the configured cap. Depth is the number of directories
between ``src/synthorg/`` and the file (exclusive of the filename).

The default cap is the current measured maximum (4 as of the PR-1
audit). Existing offenders are absorbed via
``scripts/_module_depth_baseline.txt`` (one ``path:depth`` per line).

Usage::

    uv run python scripts/check_module_depth.py
"""

import argparse
import dataclasses
import re
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_BASELINE_REL = Path("scripts") / "_module_depth_baseline.txt"
_SCAN_REL: Final[str] = "src/synthorg"

_DEFAULT_CAP: Final[int] = 4

_BASELINE_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<path>[^:#\s]+):(?P<depth>\d+)\s*(?:#.*)?$"
)

_BASELINE_HEADER = (
    "# Frozen baseline of pre-existing over-deep modules in src/synthorg/.\n"
    "# Each line is `path:depth` (POSIX path, integer depth measured from\n"
    "# src/synthorg/). The gate compares the current file's depth to the\n"
    "# baseline depth; if a file moves deeper, the gate fails.\n"
    "#\n"
    "# Regenerate (rare; requires explicit user approval) via the gate's\n"
    "# write_baseline() Python API.\n"
)


@dataclasses.dataclass(frozen=True)
class Violation:
    """One file exceeding the depth cap or its baseline depth."""

    path: str
    depth: int
    cap: int
    baseline: int | None

    def render(self) -> str:
        baseline_s = "none" if self.baseline is None else str(self.baseline)
        return f"{self.path}: depth={self.depth} cap={self.cap} baseline={baseline_s}"


def compute_depth(rel_posix: str) -> int:
    """Return the directory-depth of *rel_posix* below ``src/synthorg/``.

    ``src/synthorg/foo.py`` -> 0; ``src/synthorg/a/b.py`` -> 1; etc.
    """
    posix = rel_posix.replace("\\", "/")
    if posix.startswith("src/synthorg/"):
        rest = posix[len("src/synthorg/") :]
    else:
        rest = posix
    parts = rest.split("/")
    return max(0, len(parts) - 1)


def _load_baseline(baseline_path: Path) -> dict[str, int]:
    if not baseline_path.is_file():
        return {}
    out: dict[str, int] = {}
    for line in baseline_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _BASELINE_LINE_RE.match(stripped)
        if match is None:
            continue
        out[match.group("path")] = int(match.group("depth"))
    return out


def _iter_source_files(project_root: Path) -> list[Path]:
    scan_root = project_root / _SCAN_REL
    if not scan_root.is_dir():
        return []
    return sorted(scan_root.rglob("*.py"))


def check(
    *, project_root: Path, baseline_path: Path, cap: int = _DEFAULT_CAP
) -> list[Violation]:
    """Walk the tree and return depth violations.

    A file is a violation iff its depth strictly exceeds the cap AND
    its baseline entry (if any) is less than its current depth.
    """
    baseline = _load_baseline(baseline_path)
    violations: list[Violation] = []
    for path in _iter_source_files(project_root):
        rel = path.relative_to(project_root).as_posix()
        depth = compute_depth(rel)
        if depth <= cap:
            continue
        baseline_depth = baseline.get(rel)
        if baseline_depth is not None and depth <= baseline_depth:
            continue
        violations.append(
            Violation(path=rel, depth=depth, cap=cap, baseline=baseline_depth)
        )
    return violations


def _compute_baseline_payload(project_root: Path, cap: int) -> dict[str, int]:
    payload: dict[str, int] = {}
    for path in _iter_source_files(project_root):
        rel = path.relative_to(project_root).as_posix()
        depth = compute_depth(rel)
        if depth > cap:
            payload[rel] = depth
    return dict(sorted(payload.items()))


def write_baseline(
    *, project_root: Path, baseline_path: Path, cap: int = _DEFAULT_CAP
) -> None:
    """Regenerate the baseline file."""
    payload = _compute_baseline_payload(project_root, cap)
    body = "\n".join(f"{rel}:{depth}" for rel, depth in payload.items())
    suffix = "\n" if body else ""
    baseline_path.write_text(_BASELINE_HEADER + body + suffix, encoding="utf-8")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_REPO_ROOT_DEFAULT,
        help="Override the project root.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=f"Override the baseline path (default: {_BASELINE_REL.as_posix()}).",
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=_DEFAULT_CAP,
        help=f"Maximum allowed depth (default: {_DEFAULT_CAP}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry. ``0`` clean, ``1`` on any depth violation."""
    args = _build_arg_parser().parse_args(argv)
    project_root: Path = args.project_root.resolve()
    baseline_path: Path = (
        args.baseline.resolve()
        if args.baseline is not None
        else project_root / _BASELINE_REL
    )
    violations = check(
        project_root=project_root, baseline_path=baseline_path, cap=args.cap
    )
    if not violations:
        return 0
    print("Module depth cap exceeded:", file=sys.stderr)
    for violation in violations:
        print(f"  {violation.render()}", file=sys.stderr)
    print(
        "\nFlatten the package nesting or move the file closer to its feature root.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
