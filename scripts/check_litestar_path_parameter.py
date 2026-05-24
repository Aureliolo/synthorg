#!/usr/bin/env python3
"""Lint guard for Litestar 2.22 path / query / header parameter style.

Litestar 2.22 deprecated the inferred-style PATH-bound handler param
(``param_name: str``) and the kwarg-style ``Parameter(query=...)`` /
``Parameter(header=...)`` / ``Parameter(cookie=...)`` shorthand. Bare
``Parameter(...)`` on a PATH-bound handler param raises
``ImproperlyConfiguredException: Kwarg resolution ambiguity`` at
``create_app`` time, taking the whole API offline.

The migration in PR #2091 swept the controllers to use the typed
markers (``PathParameter``, ``QueryParameter``, ``HeaderParameter``,
``CookieParameter``) instead. This gate catches the second occurrence
of either regression:

* a PATH-bound handler param (name matches a ``{name:type}``
  placeholder from the controller / decorator path) using
  ``Parameter(...)`` instead of ``PathParameter(...)``;
* any ``Parameter(...)`` call passing the deprecated ``query=`` /
  ``header=`` / ``cookie=`` kwarg.

Both rules are AST-based, fail-closed on a SyntaxError, and carry no
baseline -- the tree is expected clean after #2091 and a genuine
pre-existing violation should be fixed, not frozen.

Per-line opt-out: append ``# lint-allow: litestar-path-parameter --
<reason>`` to a line that intentionally bypasses the gate. The reason
is mandatory non-empty; rare exemption only (e.g. a typed marker is
inadequate for a one-off shape).

Exit codes:
    0 -- every scanned site uses the correct typed marker.
    1 -- at least one regression detected. Offending sites are
         printed to stderr with a fix-it hint.
    2 -- internal error parsing a target file (script bug or a
         genuine syntax error in source).

Usage::

    python scripts/check_litestar_path_parameter.py
    python scripts/check_litestar_path_parameter.py --repo-root /path/to/repo
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

# Files scanned by the gate. ``path_params.py`` and ``pagination.py``
# define module-level Annotated aliases that the controllers consume;
# tracking them here lets Rule 3 fire even if no controller has
# regressed yet.
CONTROLLERS_ROOT = Path("src/synthorg/api/controllers")
PATH_ALIAS_MODULE = Path("src/synthorg/api/path_params.py")
QUERY_ALIAS_MODULE = Path("src/synthorg/api/pagination.py")
COCKPIT_ALIAS_MODULE = Path("src/synthorg/api/controllers/cockpit.py")

# Litestar HTTP decorators that bind a handler method to a route.
# ``route`` is the multi-verb decorator; the rest are single-verb.
_HTTP_DECORATORS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "route"}
)

# Deprecated kwargs on ``litestar.params.Parameter``. Each shorthand
# has a dedicated typed marker class in 2.22+.
_DEPRECATED_KWARGS: dict[str, str] = {
    "query": "QueryParameter(name=...)",
    "header": "HeaderParameter(name=...)",
    "cookie": "CookieParameter(name=...)",
}

# Compiled once at module import; matches ``{name:type}`` placeholders
# in route strings. The capture is the identifier; the ``:type`` part
# is consumed but not bound (it's the converter, irrelevant to name
# matching).
_ROUTE_PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\s*:[^}]+\}")

_OPT_OUT_MARKER = "lint-allow: litestar-path-parameter"


@dataclass(frozen=True)
class Violation:
    """A single regression in the scanned tree."""

    file: str
    lineno: int
    rule: str
    detail: str


def _is_opt_out(source_lines: list[str], lineno: int) -> bool:
    """Return True iff the source line carries the opt-out marker.

    The marker MUST appear on the same line as the offending call so
    a reviewer cannot accidentally suppress an unrelated future
    violation by adding the comment elsewhere in the file.
    """
    if not 1 <= lineno <= len(source_lines):
        return False
    line = source_lines[lineno - 1]
    if _OPT_OUT_MARKER not in line:
        return False
    after = line.split(_OPT_OUT_MARKER, 1)[1].strip()
    if not after.startswith("--"):
        return False
    reason = after[2:].strip()
    return bool(reason)


def _resolve_marker_name(node: ast.expr) -> str | None:
    """Return the simple name of a ``litestar.params.*`` marker call.

    Handles both ``Parameter(...)`` (``Call.func`` is ``Name``) and
    ``params.Parameter(...)`` (``Call.func`` is ``Attribute``). Returns
    the trailing identifier so the gate can match on the symbol name
    rather than the import alias.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _extract_string_literal(node: ast.expr | None) -> str:
    """Return the literal string value or ``""`` on a non-literal.

    Handlers that compose paths dynamically (string concat, format
    strings) are rare and fall back to an empty route -- the rule
    then simply cannot match placeholders against the handler's
    params, which is the safe default (no false-positive PATH match).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _controller_path(class_node: ast.ClassDef) -> str:
    """Extract the ``path = "..."`` class-attr literal, default ``""``.

    Walks only the direct class body (not nested classes). Annotated
    assignments are accepted because some controllers use
    ``path: str = "..."``.
    """
    for stmt in class_node.body:
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target_name = stmt.targets[0].id
                value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target_name = stmt.target.id
            value = stmt.value
        if target_name == "path":
            return _extract_string_literal(value)
    return ""


def _decorator_path(decorator: ast.expr) -> str:
    """Extract the route string from a Litestar HTTP decorator.

    Accepts both ``@get("/x")`` (positional) and ``@get(path="/x")``
    (keyword) forms. Returns ``""`` for non-HTTP decorators so the
    handler is silently skipped.
    """
    if not isinstance(decorator, ast.Call):
        return ""
    name = _resolve_marker_name(decorator.func)
    if name not in _HTTP_DECORATORS:
        return ""
    if decorator.args and isinstance(decorator.args[0], ast.Constant):
        return _extract_string_literal(decorator.args[0])
    for kw in decorator.keywords:
        if kw.arg == "path":
            return _extract_string_literal(kw.value)
    return ""


def _is_handler(method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A handler is decorated with a Litestar HTTP verb decorator."""
    return any(
        isinstance(dec, ast.Call) and _resolve_marker_name(dec.func) in _HTTP_DECORATORS
        for dec in method.decorator_list
    )


def _handler_route(method: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return the combined route from all HTTP decorators on the method.

    Multi-decorator handlers (rare but legal) concatenate their route
    strings; placeholders from either decorator should match the
    handler's params.
    """
    parts: list[str] = []
    for dec in method.decorator_list:
        route = _decorator_path(dec)
        if route:
            parts.append(route)
    return "".join(parts)


def _placeholder_names(route: str) -> set[str]:
    """Extract ``{name:type}`` placeholder names from a route string."""
    return set(_ROUTE_PLACEHOLDER_RE.findall(route))


def _annotation_call(annotation: ast.expr | None) -> ast.Call | None:
    """Return the marker ``Call`` inside an ``Annotated[T, X(...)]`` arg.

    Returns ``None`` for plain types (``str``, ``int``, alias names),
    for ``Annotated[T, instance]`` (rare; ``instance`` is not a
    ``Call``), or for any other shape. The caller checks whether the
    returned call is a ``Parameter`` violation.
    """
    if not isinstance(annotation, ast.Subscript):
        return None
    name = _resolve_marker_name(annotation.value)
    if name != "Annotated":
        return None
    slice_node = annotation.slice
    if not isinstance(slice_node, ast.Tuple):
        return None
    for elt in slice_node.elts[1:]:
        if isinstance(elt, ast.Call):
            return elt
    return None


def _check_path_bound_params(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    placeholders: set[str],
    rel: str,
    source_lines: list[str],
) -> list[Violation]:
    """Rule 1: every PATH-bound handler param uses PathParameter."""
    out: list[Violation] = []
    for arg in method.args.args:
        if arg.arg not in placeholders:
            continue
        marker = _annotation_call(arg.annotation)
        if marker is None:
            continue
        marker_name = _resolve_marker_name(marker.func)
        if marker_name != "Parameter":
            continue
        if _is_opt_out(source_lines, marker.lineno):
            continue
        out.append(
            Violation(
                file=rel,
                lineno=marker.lineno,
                rule="path-bound-must-use-path-parameter",
                detail=(
                    f"handler {method.name!r} param {arg.arg!r} matches "
                    f"route placeholder {{{arg.arg}:...}} but uses bare "
                    f"Parameter(...). Replace with PathParameter(...) "
                    f"(from litestar.params)."
                ),
            )
        )
    return out


def _check_deprecated_kwargs(
    call: ast.Call,
    rel: str,
    source_lines: list[str],
) -> Violation | None:
    """Rule 2: ``Parameter(query=/header=/cookie=)`` is forbidden."""
    marker_name = _resolve_marker_name(call.func)
    if marker_name != "Parameter":
        return None
    for kw in call.keywords:
        if kw.arg in _DEPRECATED_KWARGS:
            if _is_opt_out(source_lines, call.lineno):
                return None
            suggestion = _DEPRECATED_KWARGS[kw.arg]
            return Violation(
                file=rel,
                lineno=call.lineno,
                rule="deprecated-parameter-kwarg",
                detail=(
                    f"Parameter({kw.arg}=...) is deprecated in Litestar "
                    f"2.22. Replace with {suggestion}."
                ),
            )
    return None


def _check_path_alias_module(
    tree: ast.Module,
    rel: str,
    source_lines: list[str],
) -> list[Violation]:
    """Rule 3: module-level ``Path*`` aliases must wrap PathParameter."""
    out: list[Violation] = []
    for stmt in tree.body:
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target_name = stmt.targets[0].id
                value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target_name = stmt.target.id
            value = stmt.value
        if not target_name or not target_name.startswith("Path"):
            continue
        marker = _annotation_call(value) if value is not None else None
        if marker is None:
            continue
        marker_name = _resolve_marker_name(marker.func)
        if marker_name == "PathParameter":
            continue
        if marker_name != "Parameter":
            continue
        if _is_opt_out(source_lines, marker.lineno):
            continue
        out.append(
            Violation(
                file=rel,
                lineno=marker.lineno,
                rule="path-alias-must-use-path-parameter",
                detail=(
                    f"module-level alias {target_name!r} must wrap "
                    f"PathParameter(...) (aliases with the Path* prefix "
                    f"are PATH-bound by convention)."
                ),
            )
        )
    return out


def _scan_module(
    path: Path,
    repo_root: Path,
) -> list[Violation]:
    """Walk one Python source file and return any violations.

    Re-uses the parsed module across all three rules so a large
    controller file only parses once.
    """
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        msg = f"{path}: {exc}"
        raise SyntaxError(msg) from exc
    rel = path.relative_to(repo_root).as_posix()

    violations: list[Violation] = []

    if path.resolve() in {
        (repo_root / PATH_ALIAS_MODULE).resolve(),
        (repo_root / COCKPIT_ALIAS_MODULE).resolve(),
    }:
        violations.extend(_check_path_alias_module(tree, rel, source_lines))

    for class_node in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        controller_path = _controller_path(class_node)
        for method in class_node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_handler(method):
                continue
            placeholders = _placeholder_names(
                controller_path + _handler_route(method),
            )
            if placeholders:
                violations.extend(
                    _check_path_bound_params(
                        method,
                        placeholders,
                        rel,
                        source_lines,
                    )
                )

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            violation = _check_deprecated_kwargs(node, rel, source_lines)
            if violation is not None:
                violations.append(violation)

    return violations


def _iter_scanned_files(repo_root: Path) -> Iterable[Path]:
    """Yield every Python file the gate must scan.

    The controllers tree is the main surface; ``path_params.py`` and
    ``pagination.py`` are the module-level alias homes.
    """
    controllers = repo_root / CONTROLLERS_ROOT
    if controllers.is_dir():
        yield from sorted(controllers.rglob("*.py"))
    aliases = (PATH_ALIAS_MODULE, QUERY_ALIAS_MODULE)
    yielded = {(repo_root / p).resolve() for p in (CONTROLLERS_ROOT,)}
    for alias in aliases:
        path = repo_root / alias
        if path.is_file() and path.resolve() not in yielded:
            yield path


def _run(repo_root: Path) -> int:
    violations: list[Violation] = []
    for path in _iter_scanned_files(repo_root):
        violations.extend(_scan_module(path, repo_root))
    if not violations:
        return 0
    print("Litestar 2.22 parameter-style violations:")
    for v in violations:
        print(f"  {v.file}:{v.lineno} ({v.rule}) -- {v.detail}")
    print(
        "\nFix: PATH-bound handler params take `PathParameter(...)`; "
        "query / header / cookie shorthand on Parameter() is "
        "deprecated -- use QueryParameter / HeaderParameter / "
        "CookieParameter from litestar.params. Genuine exemptions add "
        "`# lint-allow: litestar-path-parameter -- <reason>` to the "
        "offending line.",
    )
    return 1


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        return _run(args.repo_root.resolve())
    except SyntaxError as exc:
        print(f"INTERNAL ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
