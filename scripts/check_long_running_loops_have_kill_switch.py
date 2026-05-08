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
  ``while`` line itself or one of the two preceding source lines
  (so the comment can sit on a leading comment block too). One
  ``lint-allow`` covers exactly one loop -- a function with two
  unguarded loops needs two markers, otherwise a function-wide
  opt-out would mask a new sibling loop added later.

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
        """Stable single-line baseline form keyed per loop.

        Module-level functions: ``<rel-path>:<func>:<lineno>``.
        Methods: ``<rel-path>:<class>:<func>:<lineno>``.

        ``lineno`` is the ``while``-loop's own line, not the enclosing
        function's. Including it means a baselined function with one
        unguarded loop cannot silently absorb a new unguarded sibling
        loop -- the new key (different lineno) lands as a fresh
        violation. Line shifts caused by unrelated edits surface as
        stale-baseline warnings (gate still passes; operator
        regenerates).
        """
        prefix = (
            f"{self.file}:{self.function}"
            if self.class_name is None
            else f"{self.file}:{self.class_name}:{self.function}"
        )
        return f"{prefix}:{self.lineno}"


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


def _walk_current_scope(node: ast.AST) -> Iterable[ast.AST]:
    """Yield ``node`` and its descendants without crossing scope boundaries.

    Stops at nested ``def``/``async def``/``class``/``lambda`` bodies so
    helpers inside the function under inspection do not appear to be
    part of its control flow. Without this, an inner closure containing
    a ``while True`` would make the outer function look long-running,
    or an inner ``_resolve_*_enabled()`` call would make an unguarded
    outer loop look compliant.
    """
    stack: list[ast.AST] = [node]
    nested_scopes = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Lambda,
    )
    while stack:
        current = stack.pop()
        yield current
        for child in ast.iter_child_nodes(current):
            if child is not node and isinstance(child, nested_scopes):
                continue
            stack.append(child)


_RESOLVER_RECEIVER_NAMES = frozenset(
    {
        "config_resolver",
        "_config_resolver",
        "resolver",
        "_resolver",
    }
)


def _is_resolver_receiver(node: ast.AST) -> bool:
    """Return True if ``node`` looks like a ConfigResolver instance.

    Accepts the canonical receiver shapes that appear in
    ``synthorg/`` kill-switch resolver helpers:

    * ``config_resolver`` (free-function helpers like
      ``_resolve_lifecycle_cleanup_enabled``)
    * ``self._config_resolver`` / ``self.config_resolver`` (instance
      method form, e.g. ``NotificationDispatcher._resolve_enabled``)
    * ``app_state.config_resolver`` (helpers that take ``app_state``)
    * ``something.resolver`` / ``something._resolver`` (defensive: a
      wrapper service exposing the resolver under those attribute
      names)

    Tightening the check this way prevents an unrelated
    ``foo.get_bool(<ns>, "x_enabled")`` call from accidentally
    satisfying the gate just because the literal ends in ``_enabled``.
    """
    if isinstance(node, ast.Name):
        return node.id in _RESOLVER_RECEIVER_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in _RESOLVER_RECEIVER_NAMES
    return False


def _calls_kill_switch_resolver(node: ast.AST) -> bool:
    """Return True if any descendant Call references the kill-switch idiom.

    Three accepted shapes:

    * ``<resolver>.get_bool(<ns>, "<key>_enabled")`` where ``<resolver>``
      is one of the canonical receiver names (see
      ``_is_resolver_receiver``).
    * ``await self._resolve_enabled()`` / ``_resolve_<x>_enabled()``
    * ``await _resolve_<x>_enabled(<arg>)`` (free-function form,
      e.g. ``_resolve_lifecycle_cleanup_enabled``)
    """
    for sub in _walk_current_scope(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Attribute):
            if (
                func.attr == "get_bool"
                and _is_resolver_receiver(func.value)
                and any(
                    isinstance(a, ast.Constant)
                    and isinstance(a.value, str)
                    and a.value.endswith("_enabled")
                    for a in sub.args
                )
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


def _has_suppression(source_lines: list[str], node: ast.AST) -> bool:
    """Return True if a ``# lint-allow:`` comment guards ``node``.

    Looks at ``node``'s own line plus the two preceding source lines so
    a developer can place the suppression on the loop's decorator, the
    loop's header, or a leading comment block without being picky about
    exact placement. Suppression is per-loop (not per-function): a
    function with multiple long-running loops needs one ``lint-allow``
    per loop, otherwise a baselined function could silently absorb a
    new unguarded sibling loop under the existing function-wide
    opt-out.
    """
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    candidate_lines = source_lines[max(0, lineno - 3) : lineno]
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
    """Return the violations present in ``path``.

    Emits one ``Violation`` per long-running ``while`` that lacks a
    kill-switch, not one per enclosing function. Without this, a
    function with one baselined unguarded loop could silently absorb a
    second unguarded sibling loop -- the per-loop ``lineno`` in
    ``Violation.key()`` is the discriminator that catches that case.
    """
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        # Fail closed: silently swallowing the parse error would skip
        # every long-running loop in this file under a pre-3.14
        # interpreter (PEP 758 multi-except is widely used in src/).
        msg = f"{path}: {exc}"
        raise SyntaxError(msg) from exc
    source_lines = text.splitlines()
    rel = path.relative_to(repo_root).as_posix()
    violations: list[Violation] = []
    for func, class_name in _iter_async_funcs(tree):
        whiles: list[ast.While] = [
            w
            for w in _walk_current_scope(func)
            if isinstance(w, ast.While) and _is_long_running_while(w)
        ]
        if not whiles:
            continue
        for w in whiles:
            if _calls_kill_switch_resolver(w):
                continue
            if _has_suppression(source_lines, w):
                continue
            violations.append(
                Violation(
                    file=rel,
                    function=func.name,
                    lineno=w.lineno,
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
        "# One ``<relpath>:<func>:<lineno>`` (module-level) or\n"
        "# ``<relpath>:<class>:<func>:<lineno>`` (method) per line.\n"
        "# ``<lineno>`` is the while-loop's own line so a baselined\n"
        "# function cannot silently absorb a new unguarded sibling loop.\n"
        "# New violations not on this list fail the gate; entries here\n"
        "# that no longer match the scan emit a stale-baseline warning\n"
        "# (gate still passes; regenerate via --update-baseline).\n"
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
            f" add ``# lint-allow: {GATE_NAME} -- <reason>`` on the"
            " ``while`` line itself or one of the two preceding"
            " source lines (the suppression check is per-loop, not"
            " per-function).",
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
