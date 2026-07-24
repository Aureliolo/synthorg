"""Gate: forge agent tools enforce the connection's repository scope.

Forge tools are least-privilege and fail-closed: every call must be
checked against the bound connection's ``allowed_repos`` before egress,
so an org-wide token cannot act on any repository it can merely reach.
The check lives once in ``_BaseForgeTool._resolve_connection`` (which
raises :class:`ForgeRepoScopeError` for an out-of-scope ``owner/repo``),
and the whole forge family inherits it.

This gate guards two regressions:

1. The shared enforcement disappearing -- ``_BaseForgeTool`` must define a
   ``_resolve_connection`` override whose body *reachably raises*
   ``ForgeRepoScopeError``.
2. A forge tool *bypassing* it -- any class under ``tools/forge/`` that
   overrides ``_resolve_connection`` must either delegate to
   ``super()._resolve_connection(...)`` or re-enforce the scope itself
   with its own reachable raise.

Both checks are behavioural rather than token-based: a mere mention of
``ForgeRepoScopeError`` (an ``except`` handler, an unused import, a
docstring) leaves every repository admitted while satisfying a naive
search, and a raise parked after an unconditional ``return`` never runs.

Opt a genuine exception out with a trailing
``# lint-allow: forge-repo-scoped -- <reason>`` comment on the class's
``class`` line.

Usage:
    uv run python scripts/check_forge_repo_scoped.py

Exit codes:
    0 -- forge repo-scope enforcement is intact.
    1 -- the enforcement is missing or bypassed.
    2 -- configuration error (bad ``--repo-root`` or an unreadable source).
"""

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        reachable_statements,
        read_and_parse,
        statement_expressions,
    )
else:
    from scripts._gate_source import (
        GateSourceError,
        reachable_statements,
        read_and_parse,
        statement_expressions,
    )

_FORGE_REL: Final[str] = "src/synthorg/tools/forge"
_BASE_REL: Final[str] = "src/synthorg/tools/forge/_base.py"
_RESOLVE_FN: Final[str] = "_resolve_connection"
_SCOPE_ERROR: Final[str] = "ForgeRepoScopeError"
_BASE_CLASS: Final[str] = "_BaseForgeTool"
_SUPER: Final[str] = "super"
_MARKER: Final[str] = "lint-allow: forge-repo-scoped"

type _Method = ast.FunctionDef | ast.AsyncFunctionDef

_METHOD_TYPES: Final = (ast.FunctionDef, ast.AsyncFunctionDef)
_ALLOW_RE: Final[re.Pattern[str]] = re.compile(
    r"#.*" + re.escape(_MARKER) + r"\s*--\s*\S"
)


def _resolve_method(node: ast.ClassDef) -> _Method | None:
    """Return the class's ``_resolve_connection`` override, if any.

    Both ``def`` and ``async def`` count: a synchronous override bypasses
    the scope check exactly as an asynchronous one would, so matching only
    the coroutine form would leave a trivial hole.

    Returns:
        The method node, or ``None`` when the class does not override it.
    """
    for stmt in node.body:
        if isinstance(stmt, _METHOD_TYPES) and stmt.name == _RESOLVE_FN:
            return stmt
    return None


def _exception_name(exc: ast.expr) -> str | None:
    """Return the name of the exception a ``raise`` constructs.

    Returns:
        The exception class name, or ``None`` for an unusual expression.
    """
    node = exc.func if isinstance(exc, ast.Call) else exc
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _raises_scope_error(method: _Method) -> bool:
    """Whether the method has a reachable ``raise ForgeRepoScopeError``.

    A bare name reference is not enough: an ``except ForgeRepoScopeError``
    handler, an unused import, or a docstring mention would all satisfy a
    token search while the tool silently admitted every repository.

    Returns:
        ``True`` when a reachable statement raises the scope error.
    """
    for stmt in reachable_statements(method.body):
        if (
            isinstance(stmt, ast.Raise)
            and stmt.exc is not None
            and _exception_name(stmt.exc) == _SCOPE_ERROR
        ):
            return True
    return False


def _delegates_to_base(method: _Method) -> bool:
    """Whether the override reaches the shared check via ``super()``.

    Returns:
        ``True`` when a reachable statement calls
        ``super()._resolve_connection(...)``.
    """
    for stmt in reachable_statements(method.body):
        for node in statement_expressions(stmt):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != _RESOLVE_FN:
                continue
            base = func.value
            if (
                isinstance(base, ast.Call)
                and isinstance(base.func, ast.Name)
                and base.func.id == _SUPER
            ):
                return True
    return False


def _class_line_has_marker(source: str, node: ast.ClassDef) -> bool:
    """Whether the class's ``class`` line carries the opt-out marker.

    Returns:
        ``True`` when a valid ``# lint-allow: forge-repo-scoped -- ...``
        comment is on the class definition line.
    """
    line = source.splitlines()[node.lineno - 1]
    return bool(_ALLOW_RE.search(line))


def _check(repo_root: Path) -> list[str]:
    """Scan the forge tools package for scope-enforcement regressions.

    Returns:
        A list of human-readable violation messages (empty when clean).
    """
    violations: list[str] = []
    forge_dir = repo_root / _FORGE_REL
    base_ok = False
    for path in sorted(forge_dir.rglob("*.py")):
        source, tree = read_and_parse(path)
        rel = path.relative_to(repo_root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            method = _resolve_method(node)
            if method is None:
                continue
            if node.name == _BASE_CLASS:
                base_ok = _raises_scope_error(method)
                continue
            if _delegates_to_base(method) or _raises_scope_error(method):
                continue
            if _class_line_has_marker(source, node):
                continue
            violations.append(
                f"{rel}:{node.lineno}: {node.name} overrides {_RESOLVE_FN} "
                f"without delegating to {_SUPER}().{_RESOLVE_FN} or raising "
                f"{_SCOPE_ERROR} (repo-scope bypass)"
            )
    if not base_ok:
        violations.append(
            f"{_BASE_REL}: {_BASE_CLASS} must override {_RESOLVE_FN} with a "
            f"reachable raise of {_SCOPE_ERROR} so forge tools stay "
            f"repo-scoped (fail-closed)"
        )
    return violations


def main() -> int:
    """Run the forge repo-scope gate.

    Returns:
        The process exit code (0 clean, 1 violations, 2 config error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        violations = _check(args.repo_root)
    except GateSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if violations:
        print("Forge repo-scope enforcement check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
