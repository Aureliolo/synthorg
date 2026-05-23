#!/usr/bin/env python3
"""Tiered module-size budget gate.

Walks ``src/synthorg/`` and verifies every Python file fits its tier
cap. Tiers are declared per-file via ``# module-kind: <tier>`` header
on the first non-blank, non-shebang, non-encoding-declaration line.
Files without a header default to the ``code`` tier (500 LOC).

Existing oversized files are absorbed via
``scripts/_module_size_baseline.json``. A file may stay at its
baseline LOC; growing past the baseline fails. New files may not
exceed their tier cap regardless of baseline.

LOC counting strips blank lines and comment-only lines (mirrors
``check_baseline_growth.py::_count_text_entries``); inline trailing
comments do count.

Tier caps come from :data:`_module_size_lib.TIER_LIMITS`.

Usage::

    uv run python scripts/check_module_size_budget.py
    uv run python scripts/check_module_size_budget.py --update-baseline

The ``--update-baseline`` mode regenerates
``scripts/_module_size_baseline.json`` from the current tree. Running
twice produces an identical file (idempotent).
"""

import argparse
import dataclasses
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_BASELINE_REL = Path("scripts") / "_module_size_baseline.json"
_SCAN_REL = Path("src") / "synthorg"

_BASELINE_DESCRIPTION = (
    "Frozen baseline of oversized src/synthorg/ modules. Each entry under "
    "`locations` is `<posix_path>: <current_loc>`. Modules absent from the "
    "baseline are enforced strictly against their tier cap. Modules present "
    "here may stay at or below the recorded LOC; growing past it fails the "
    "gate. Regenerate (rare; requires explicit user approval) with: "
    "uv run python scripts/check_module_size_budget.py --update-baseline"
)


def _load_lib() -> ModuleType:
    lib_path = Path(__file__).resolve().parent / "_module_size_lib.py"
    spec = importlib.util.spec_from_file_location("_module_size_lib", lib_path)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {lib_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LIB: Any = cast("Any", _load_lib())


@dataclasses.dataclass(frozen=True)
class Violation:
    """One file exceeding its tier cap or baseline."""

    path: str
    tier: str
    loc: int
    cap: int | None
    baseline: int | None

    def render(self) -> str:
        """Format for stderr: ``<path>: tier=<t> loc=<n> cap=<c> baseline=<b>``."""
        cap_s = "exempt" if self.cap is None else str(self.cap)
        baseline_s = "none" if self.baseline is None else str(self.baseline)
        return (
            f"{self.path}: tier={self.tier} loc={self.loc} "
            f"cap={cap_s} baseline={baseline_s}"
        )


def _iter_source_files(project_root: Path) -> list[Path]:
    """Return every ``*.py`` file under ``src/synthorg/`` sorted lexically."""
    scan_root = project_root / _SCAN_REL
    if not scan_root.is_dir():
        return []
    return sorted(scan_root.rglob("*.py"))


def _load_baseline(baseline_path: Path) -> dict[str, int]:
    """Parse the baseline JSON; return ``{path: loc}`` mapping.

    Baseline schema is ``{"description": str, "locations": {path: loc}}``.
    Flat ``{path: loc}`` JSON is also accepted as a shorthand the test
    suite uses; production baselines always carry the description.

    Args:
        baseline_path: Path to the baseline file.

    Returns:
        Mapping of POSIX repo-relative path to recorded LOC.

    Raises:
        ValueError: If the file is not a JSON object.
    """
    if not baseline_path.is_file():
        return {}
    text = baseline_path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    payload = json.loads(text)
    if not isinstance(payload, dict):
        msg = f"baseline {baseline_path} must be a JSON object"
        raise ValueError(msg)
    raw = payload.get("locations", payload)
    if not isinstance(raw, dict):
        msg = f"baseline {baseline_path} 'locations' must be a JSON object"
        raise ValueError(msg)
    return {
        str(key): int(value) for key, value in raw.items() if isinstance(value, int)
    }


def _relpath_posix(path: Path, project_root: Path) -> str:
    return path.relative_to(project_root).as_posix()


def check(*, project_root: Path, baseline_path: Path) -> list[Violation]:
    """Run the gate and return every violation.

    Args:
        project_root: Resolved repo root.
        baseline_path: Path to the JSON baseline file.

    Returns:
        Violations in source order (deterministic).
    """
    baseline = _load_baseline(baseline_path)
    violations: list[Violation] = []
    for path in _iter_source_files(project_root):
        tier = _LIB.resolve_tier(path, project_root=project_root)
        if tier == "generated":
            continue
        cap: int | None = _LIB.TIER_LIMITS[tier]
        loc = _LIB.count_loc(path)
        rel = _relpath_posix(path, project_root)
        baseline_loc = baseline.get(rel)
        if (
            cap is not None
            and loc > cap
            and (baseline_loc is None or loc > baseline_loc)
        ):
            violations.append(
                Violation(path=rel, tier=tier, loc=loc, cap=cap, baseline=baseline_loc)
            )
    return violations


def _compute_baseline_payload(project_root: Path) -> dict[str, int]:
    """Return ``{rel_path: loc}`` for every file currently over its cap."""
    payload: dict[str, int] = {}
    for path in _iter_source_files(project_root):
        tier = _LIB.resolve_tier(path, project_root=project_root)
        if tier == "generated":
            continue
        cap: int | None = _LIB.TIER_LIMITS[tier]
        if cap is None:
            continue
        loc = _LIB.count_loc(path)
        if loc > cap:
            payload[_relpath_posix(path, project_root)] = loc
    return dict(sorted(payload.items()))


def write_baseline(*, project_root: Path, baseline_path: Path) -> None:
    """Regenerate the baseline file from the current tree.

    Output is sorted-deterministic with the standard description block
    so the schema is self-describing. Running twice on the same tree
    produces identical bytes.
    """
    locations = _compute_baseline_payload(project_root)
    payload = {
        "description": _BASELINE_DESCRIPTION,
        "locations": locations,
    }
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    baseline_path.write_text(body, encoding="utf-8")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_REPO_ROOT_DEFAULT,
        help="Override the project root (default: scripts/.. relative to this file)",
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
        help="Regenerate the baseline file from the current tree and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` on clean tree; ``1`` if any file exceeds its cap and baseline.
    """
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
    for violation in violations:
        print(violation.render(), file=sys.stderr)
    print(
        "\nModule-size budget gate failed. Options:\n"
        "  * Shrink the file under its tier cap.\n"
        "  * Add a `# module-kind: <tier>` header if the file's tier was "
        "misclassified.\n"
        "  * If the file is genuinely declarative, add "
        "`# module-kind: declarative`.\n"
        "Do NOT regenerate the baseline without explicit user approval.\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
