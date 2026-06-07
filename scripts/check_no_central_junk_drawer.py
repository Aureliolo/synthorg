#!/usr/bin/env python3
"""No-central-junk-drawer gate.

Two modules in ``src/synthorg/`` accumulate everything from unrelated
domains today:

* ``src/synthorg/core/enums.py``: 57 StrEnums across 8 domains.
* ``src/synthorg/api/state.py``: the giant ``AppState`` attribute-bag
  via ``__slots__``.

This gate locks today's counts via ``scripts/_central_junk_drawer_baseline.json``
and fails the build if any count grows. Net-decreases are allowed.

Counting rules:

* enums.py: top-level ``ClassDef`` nodes.
* api/state.py: aggregate length of every class body's ``__slots__``
  tuple/list.

These files are being dissolved into their feature directories; this
gate exists so no new entries accumulate in them until then.

Usage::

    uv run python scripts/check_no_central_junk_drawer.py
"""

import argparse
import ast
import dataclasses
import json
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_BASELINE_REL = Path("scripts") / "_central_junk_drawer_baseline.json"

_ENUMS_REL: Final[str] = "src/synthorg/core/enums.py"
_STATE_REL: Final[str] = "src/synthorg/api/state.py"

_BASELINE_DESCRIPTION = (
    "Frozen counts of central junk-drawer modules. Each entry under "
    "`counts` is `<posix_path>: {metric_name: current_count}`. Modules "
    "may shrink; growth fails the gate. Regenerate with "
    "`check_no_central_junk_drawer.py --update-baseline`."
)


@dataclasses.dataclass(frozen=True)
class Violation:
    """One file whose junk-drawer count grew past its baseline."""

    path: str
    metric: str
    baseline: int
    current: int

    def render(self) -> str:
        """Format for stderr: ``<path>: <metric> <baseline> -> <current>``."""
        return (
            f"{self.path}: {self.metric} {self.baseline} -> {self.current} "
            f"(+{self.current - self.baseline})"
        )


def _parse_or_empty(source: str) -> ast.Module | None:
    """Parse *source* into an AST module; return ``None`` on syntax error."""
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def count_top_level_classes(source: str) -> int:
    """Count module-level ``ClassDef`` nodes; nested defs are excluded."""
    tree = _parse_or_empty(source)
    if tree is None:
        return 0
    return sum(1 for node in tree.body if isinstance(node, ast.ClassDef))


def count_state_slots(source: str) -> int:
    """Sum of every class's ``__slots__`` tuple/list length in *source*."""
    tree = _parse_or_empty(source)
    if tree is None:
        return 0
    total = 0
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            total += _count_class_slots(node)
    return total


def _count_class_slots(class_node: ast.ClassDef) -> int:
    """Return the number of names in *class_node*'s ``__slots__`` assignment."""
    for item in class_node.body:
        if not isinstance(item, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__slots__"
            for target in item.targets
        ):
            continue
        if isinstance(item.value, (ast.Tuple, ast.List, ast.Set)):
            return sum(
                1
                for element in item.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    return 0


def _read_source(project_root: Path, rel: str) -> str:
    """Return source for a repo-relative path; empty string if missing."""
    path = project_root / rel
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _current_counts(project_root: Path) -> dict[str, dict[str, int]]:
    """Compute today's counts for every tracked junk-drawer file."""
    return {
        _ENUMS_REL: {
            "top_level_classes": count_top_level_classes(
                _read_source(project_root, _ENUMS_REL)
            ),
        },
        _STATE_REL: {
            "state_slots": count_state_slots(_read_source(project_root, _STATE_REL)),
        },
    }


def _load_baseline(baseline_path: Path) -> dict[str, dict[str, int]]:
    if not baseline_path.is_file():
        return {}
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"baseline {baseline_path} must be a JSON object"
        raise ValueError(msg)
    counts_obj = payload.get("counts", {})
    if not isinstance(counts_obj, dict):
        return {}
    out: dict[str, dict[str, int]] = {}
    for path, metric_map in counts_obj.items():
        if isinstance(path, str) and isinstance(metric_map, dict):
            out[path] = {
                metric: int(count)
                for metric, count in metric_map.items()
                if isinstance(metric, str) and isinstance(count, int)
            }
    return out


def check(*, project_root: Path, baseline_path: Path) -> list[Violation]:
    """Run the gate and return any growth violations."""
    baseline = _load_baseline(baseline_path)
    current = _current_counts(project_root)
    violations: list[Violation] = []
    for path, metrics in current.items():
        baseline_metrics = baseline.get(path, {})
        for metric_name, current_count in metrics.items():
            baseline_count = baseline_metrics.get(metric_name, 0)
            if current_count > baseline_count:
                violations.append(
                    Violation(
                        path=path,
                        metric=metric_name,
                        baseline=baseline_count,
                        current=current_count,
                    )
                )
    return sorted(violations, key=lambda v: (v.path, v.metric))


def write_baseline(*, project_root: Path, baseline_path: Path) -> None:
    """Regenerate the baseline file from the current tree."""
    payload = {
        "description": _BASELINE_DESCRIPTION,
        "counts": _current_counts(project_root),
    }
    baseline_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


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
        help=(
            "Path to the baseline JSON file (default: "
            f"<project-root>/{_BASELINE_REL.as_posix()})"
        ),
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from the current tree, then exit 0.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. ``0`` clean, ``1`` on any growth."""
    args = _build_arg_parser().parse_args(argv)
    project_root: Path = args.project_root.resolve()
    baseline_path: Path = (
        args.baseline.resolve()
        if args.baseline is not None
        else project_root / _BASELINE_REL
    )
    if args.update_baseline:
        write_baseline(project_root=project_root, baseline_path=baseline_path)
        return 0
    violations = check(project_root=project_root, baseline_path=baseline_path)
    if not violations:
        return 0
    print("Central junk-drawer counts must not grow. Violations:", file=sys.stderr)
    for violation in violations:
        print(f"  {violation.render()}", file=sys.stderr)
    print(
        "\nPut new constants / enums / state attributes in their feature "
        "directory, not in the central junk-drawer files.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
