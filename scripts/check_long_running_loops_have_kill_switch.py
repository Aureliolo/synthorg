#!/usr/bin/env python3
"""Pre-push / CI gate: every long-running async loop has a kill-switch.

Operators must be able to pause every long-running service via a
setting without restarting the process. The canonical pattern is a
per-iteration ``ConfigResolver.get_bool(..., "*_enabled")`` re-read
that fail-safes to ``True`` on resolver outage; see
``docs/reference/configuration-precedence.md`` for the full idiom.

This gate flags every ``async def`` whose body contains a
``while True:`` or ``while not <stop_event>.is_set():`` loop and that
does NOT either:

* call ``ConfigResolver.get_bool(..., "*_enabled")`` (or a helper
  named ``_resolve_*_enabled``) inside the loop body, OR
* carry a per-line opt-out comment
  ``# lint-allow: long-running-loop-kill-switch -- <reason>`` on the
  function definition (or the enclosing decorator/comment block).

The opt-out is for genuinely-not-pause-able loops (lifecycle drains,
single-shot bootstrap watchers).  The reason text is mandatory and
must be non-empty.

Baseline allowlist: ``scripts/long_running_loops_kill_switch_baseline.txt``
freezes pre-existing loops so the gate can ship without forcing a
sweeping cleanup in the same PR. Behaviour: pass when current
violations ⊆ baseline, fail when new violations appear, warn (but
pass) when baseline entries are stale (the loop now has a kill-switch
or has been deleted). Regenerate via ``--update-baseline``.

Usage::

    python scripts/check_long_running_loops_have_kill_switch.py
    python scripts/check_long_running_loops_have_kill_switch.py --repo-root /path/to/repo
    python scripts/check_long_running_loops_have_kill_switch.py --update-baseline
"""

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

GATE_NAME = "long-running-loop-kill-switch"
SUPPRESSION_RE = re.compile(
    rf"#\s*lint-allow:\s*{re.escape(GATE_NAME)}\s*--\s*\S",
)
SCAN_ROOT = Path("src/synthorg")
BASELINE_REL = Path("scripts/long_running_loops_kill_switch_baseline.txt")


@dataclass(frozen=True)
class Violation:
    """One long-running loop missing a kill-switch."""

    file: str
    function: str
    lineno: int
    class_name: str | None = None

    def key(self) -> str:
        """Stable single-line baseline form.

        Module-level functions: ``<rel-path>:<func>``.
        Methods: ``<rel-path>:<class>:<func>`` so identically-named
        methods on different classes in the same file produce distinct
        keys (otherwise a brand-new violation in a sibling class is
        masked by the first entry that lands).
        """
        if self.class_name is None:
            return f"{self.file}:{self.function}"
        return f"{self.file}:{self.class_name}:{self.function}"


def _is_long_running_while(node: ast.AST) -> bool:
    """Return True if ``node`` is a ``while True:`` or stop-event guard."""
    if not isinstance(node, ast.While):
        return False
    test = node.test
    if isinstance(test, ast.Constant) and test.value is True:
        return True
    return (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and isinstance(test.operand, ast.Call)
        and isinstance(test.operand.func, ast.Attribute)
        and test.operand.func.attr == "is_set"
    )


def _calls_kill_switch_resolver(node: ast.AST) -> bool:
    """Return True if any descendant Call references the kill-switch idiom.

    Three accepted shapes:

    * ``...config_resolver.get_bool(<ns>, "<key>_enabled")``
    * ``await self._resolve_enabled()`` / ``_resolve_<x>_enabled()``
    * ``await _resolve_<x>_enabled(<arg>)`` (free-function form,
      e.g. ``_resolve_lifecycle_cleanup_enabled``)
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Attribute):
            if func.attr == "get_bool" and any(
                isinstance(a, ast.Constant)
                and isinstance(a.value, str)
                and a.value.endswith("_enabled")
                for a in sub.args
            ):
                return True
            if func.attr.startswith("_resolve_") and func.attr.endswith("_enabled"):
                return True
        if (
            isinstance(func, ast.Name)
            and func.id.startswith("_resolve_")
            and func.id.endswith("_enabled")
        ):
            return True
    return False


def _has_suppression(source_lines: list[str], func: ast.AsyncFunctionDef) -> bool:
    """Return True if a ``# lint-allow:`` comment guards this function.

    Looks at the def line itself plus the two preceding source lines so
    a developer can place the suppression on the decorator, the
    function header, or a leading comment block without being picky
    about exact placement.
    """
    candidate_lines = source_lines[max(0, func.lineno - 3) : func.lineno]
    return any(SUPPRESSION_RE.search(line) for line in candidate_lines)


def _iter_async_funcs(
    tree: ast.AST,
    class_name: str | None = None,
) -> Iterable[tuple[ast.AsyncFunctionDef, str | None]]:
    """Yield each async function paired with its directly enclosing class name.

    Walks the AST recursively, resetting the class context on every
    ``ClassDef`` encountered. Functions defined at module scope or
    nested in another function (closure helpers) yield ``None``;
    methods defined directly under a class yield that class's name.
    """
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            yield from _iter_async_funcs(node, node.name)
        elif isinstance(node, ast.AsyncFunctionDef):
            yield node, class_name
            yield from _iter_async_funcs(node, class_name)
        else:
            yield from _iter_async_funcs(node, class_name)


def _scan_file(path: Path, repo_root: Path) -> list[Violation]:
    """Return the violations present in ``path``."""
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []  # pragma: no cover -- handled by ruff elsewhere
    source_lines = text.splitlines()
    rel = path.relative_to(repo_root).as_posix()
    violations: list[Violation] = []
    for func, class_name in _iter_async_funcs(tree):
        whiles = [w for w in ast.walk(func) if _is_long_running_while(w)]
        if not whiles:
            continue
        # Every long-running while in the function must call the
        # kill-switch resolver; a single guarded loop must not mask an
        # unguarded sibling in the same body.
        if all(_calls_kill_switch_resolver(w) for w in whiles):
            continue
        if _has_suppression(source_lines, func):
            continue
        violations.append(
            Violation(
                file=rel,
                function=func.name,
                lineno=func.lineno,
                class_name=class_name,
            ),
        )
    return violations


def _scan_repo(repo_root: Path) -> list[Violation]:
    """Scan ``src/synthorg/`` and return every violation."""
    violations: list[Violation] = []
    for py in sorted((repo_root / SCAN_ROOT).rglob("*.py")):
        violations.extend(_scan_file(py, repo_root))
    return violations


def _load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _write_baseline(path: Path, keys: Iterable[str]) -> None:
    sorted_keys = sorted(set(keys))
    header = (
        "# Frozen long-running loops without a kill-switch.\n"
        "# One ``<relpath>:<func>`` (module-level) or\n"
        "# ``<relpath>:<class>:<func>`` (method) per line. New violations\n"
        "# not on this list fail the gate; entries here that no longer\n"
        "# match the scan emit a stale-baseline warning.\n"
    )
    body = "\n".join(sorted_keys)
    path.write_text(header + body + ("\n" if sorted_keys else ""), encoding="utf-8")


def _run(repo_root: Path, baseline_path: Path, *, update_baseline: bool) -> int:
    """Scan, compare against baseline, return process exit code."""
    violations = _scan_repo(repo_root)
    keys = {v.key() for v in violations}
    if update_baseline:
        _write_baseline(baseline_path, keys)
        print(f"Wrote {len(keys)} entries to {baseline_path}")
        return 0
    baseline = _load_baseline(baseline_path)
    new = sorted(keys - baseline)
    stale = sorted(baseline - keys)
    if new:
        print("New long-running loops missing a kill-switch:")
        for v in violations:
            if v.key() in new:
                print(f"  {v.file}:{v.lineno} {v.function}")
        print(
            "\nFix: gate the loop body on a ``ConfigResolver.get_bool"
            "(<ns>, '<x>_enabled')`` re-read (fail-safe to enabled), or"
            f" add ``# lint-allow: {GATE_NAME} -- <reason>`` to the def line.",
        )
        return 1
    if stale:
        print(
            "Warning: stale baseline entries (no longer violated). "
            "Regenerate via 'python scripts/check_long_running_loops_have_kill_switch.py"
            " --update-baseline':",
        )
        for entry in stale:
            print(f"  {entry}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    summary = (__doc__ or "").splitlines()[0] if __doc__ else ""
    parser = argparse.ArgumentParser(description=summary)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite the baseline with the current scan; commit the diff explicitly.",
    )
    return parser


def main() -> int:
    """CLI entry point: scan + compare + exit-code translation."""
    args = _build_parser().parse_args()
    repo_root = args.repo_root.resolve()
    baseline_path = repo_root / BASELINE_REL
    return _run(repo_root, baseline_path, update_baseline=args.update_baseline)


if __name__ == "__main__":
    sys.exit(main())
