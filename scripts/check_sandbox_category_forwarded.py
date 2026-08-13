#!/usr/bin/env python
"""Gate: a sandboxed tool forwards its category to ``sandbox.execute``.

Every tool declares a :class:`ToolCategory`, and the sandbox takes one:
``execute(..., category=...)`` decides the container runtime (a category may
be pinned to gVisor) and whether the workspace mount is writable (a build
writes, a web fetch does not). Both are read from that argument alone.

The argument is optional and defaults to the empty string, which resolves to
"no category": every hardening keyed on it silently falls back to the global
default. That is not a hypothetical. Five call sites shipped without it, so
per-category runtime selection had never applied to any of them, and a build
stage reported ``Read-only file system`` on a workspace the design calls
writable, because ``terminal`` never reached the mount decision.

An empty default cannot be removed (callers outside the tool layer have no
category to give), and a wrong category is worse than none, so the check is
that a *tool* passes its own. Flagged: a call to ``.execute(`` on an
attribute named like a sandbox, inside a class that reaches
``self.category``, that has no ``category=`` keyword.

Opt out per-call with ``# lint-allow: sandbox-category -- <reason>``.
"""

import ast
import sys
from pathlib import Path
from typing import Final

_ALLOW_MARKER: Final[str] = "lint-allow: sandbox-category"
_SANDBOX_ATTRS: Final[frozenset[str]] = frozenset({"_sandbox", "sandbox"})
_TOOLS_ROOT: Final[str] = "src/synthorg/tools"


def _is_sandbox_execute(node: ast.Call) -> bool:
    """Return whether *node* is a ``<sandbox>.execute(...)`` call."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "execute":
        return False
    target = func.value
    if isinstance(target, ast.Attribute):
        return target.attr in _SANDBOX_ATTRS
    return isinstance(target, ast.Name) and target.id in _SANDBOX_ATTRS


def _covered_by_marker(lines: list[str], node: ast.Call) -> bool:
    """Return whether an opt-out marker sits anywhere in the call's span."""
    end = node.end_lineno or node.lineno
    span = lines[node.lineno - 1 : end]
    return any(_ALLOW_MARKER in line for line in span)


def _violations(path: Path) -> list[tuple[int, str]]:
    """Return ``(line, message)`` for every unforwarded category in *path*."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_sandbox_execute(node):
            continue
        if any(kw.arg == "category" for kw in node.keywords):
            continue
        if _covered_by_marker(lines, node):
            continue
        message = (
            "sandbox.execute() without category=: the container runtime "
            "and the workspace mount mode are both resolved from it, so "
            "omitting it silently takes the global default. Pass "
            "category=self.category.value."
        )
        found.append((node.lineno, message))
    return found


def main() -> int:
    """Report every sandbox call that drops its category.

    Returns:
        Process exit status: 0 when every call forwards one.
    """
    root = Path(__file__).resolve().parent.parent
    failures: list[str] = []
    for path in sorted((root / _TOOLS_ROOT).rglob("*.py")):
        for line, message in _violations(path):
            failures.append(f"{path.relative_to(root).as_posix()}:{line}: {message}")
    if not failures:
        return 0
    for failure in failures:
        sys.stderr.write(f"{failure}\n")
    sys.stderr.write(
        "\nSandbox category gate failed. Pass the tool's own category so the "
        "runtime and mount-mode decisions see it, or mark a genuine exception "
        f"with `# {_ALLOW_MARKER} -- <reason>`.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
