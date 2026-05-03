#!/usr/bin/env python3
"""Phase 3 of RFC #1711: lint guard for the typed-boundary contract.

The six security-sensitive entry points enumerated below MUST validate
inbound payloads through :func:`synthorg.api.boundary.parse_typed`. A
regression here re-introduces a ``dict[str, Any]`` shaped boundary --
exactly the surface that caused the audit finding behind this RFC.

The checker walks each registered (file, function) pair, finds the
``FunctionDef`` (or ``AsyncFunctionDef``) node, and confirms its body
contains at least one ``parse_typed(...)`` call. The check is presence-
only: the helper itself logs and re-raises ``ValidationError``, so a
caller that routes through it inherits the boundary contract.

Per-line opt-out: append ``# lint-allow: boundary-typed -- <reason>``
to a line that intentionally bypasses the gate. A genuine bypass MUST
be reviewed and the reason must be human-readable; the gate accepts
the marker but never silences the audit trail in the boundary helper.

Exit codes:
    0 -- every registered boundary calls ``parse_typed``.
    1 -- one or more registered boundaries no longer route through
         the helper. Offending sites printed to stderr.
    2 -- internal error parsing a source file (bug in this script
         or a syntax error in the target file).
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (relative_file_path, function_name, boundary_label) tuples enumerating
# the registered API boundaries that MUST route through parse_typed.
# Adding a new boundary means adding a tuple here AND wiring the
# corresponding parse_typed call at the named function.
_REGISTERED_BOUNDARIES: tuple[tuple[str, str, str], ...] = (
    ("src/synthorg/api/auth/service.py", "decode_token", "jwt"),
    (
        "src/synthorg/api/controllers/settings.py",
        "import_security_config",
        "settings.security",
    ),
    ("src/synthorg/api/controllers/ws_protocol.py", "handle_message", "ws.control"),
    ("src/synthorg/observability/audit_chain/sink.py", "emit", "audit_chain"),
    ("src/synthorg/a2a/rpc_params.py", "parse_rpc_params", "a2a.jsonrpc"),
    ("src/synthorg/meta/mcp/invoker.py", "invoke", "mcp.tool"),
)

_OPT_OUT_MARKER = "lint-allow: boundary-typed"


def _function_node(
    tree: ast.Module,
    function_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the unique named top-level or direct class method.

    ``ast.walk`` would happily return a nested helper or a second
    method that shadows the registered name, which is exactly the
    failure mode this gate is supposed to catch. Restrict the search
    to module-level definitions and direct class-body methods, and
    raise on ambiguity so the boundary symbol is resolved
    unambiguously.
    """
    matches: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == function_name:
                matches.append(node)
            continue
        if isinstance(node, ast.ClassDef):
            matches.extend(
                child
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == function_name
            )
    if len(matches) > 1:
        msg = (
            f"ambiguous registered boundary function {function_name!r}: "
            f"{len(matches)} top-level / direct-class definitions found"
        )
        raise ValueError(msg)
    return matches[0] if matches else None


_PARSE_TYPED_FQN = "synthorg.api.boundary.parse_typed"
_BOUNDARY_MODULE_FQN = "synthorg.api.boundary"


def _build_import_map(tree: ast.Module) -> dict[str, str]:
    """Map module-level local names to fully-qualified module paths.

    Walks top-level :class:`ast.Import` and :class:`ast.ImportFrom`
    nodes. Each binding -- including aliases -- ends up keyed by its
    in-scope name (``parse_typed`` or ``boundary`` or whatever ``as``
    aliasing chose) and resolves to the dotted FQN.

    Examples::

        from synthorg.api.boundary import parse_typed
        # -> {"parse_typed": "synthorg.api.boundary.parse_typed"}

        from synthorg.api import boundary as bnd
        # -> {"bnd": "synthorg.api.boundary"}

        import synthorg.api.boundary as bm
        # -> {"bm": "synthorg.api.boundary"}

    The map is the only authority the gate uses to decide whether a
    call site references the canonical helper, so a local shim like
    ``parse_typed = lambda *a: True`` cannot satisfy the contract.
    """
    imports: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name
                imports[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                imports[local] = f"{module}.{alias.name}" if module else alias.name
    return imports


def _resolves_to_parse_typed(
    call: ast.Call, imports: dict[str, str], *, shadowed: bool
) -> bool:
    """Return True iff ``call.func`` resolves to the canonical helper.

    Accepts two callee shapes:

    * ``ast.Name("parse_typed")`` whose binding via the module-level
      import map is the FQN of the canonical helper, AND no local
      shadowing has overridden it inside the boundary function.
    * ``ast.Attribute(value=ast.Name("X"), attr="parse_typed")`` where
      ``X`` resolves via the import map to the canonical
      ``synthorg.api.boundary`` module (covers
      ``boundary.parse_typed(...)`` qualified usage).

    Token-only matches are deliberately NOT accepted: a Pythonic
    rebinding (``parse_typed = some_other_function``) or a stray
    helper named ``parse_typed`` would otherwise green-light the gate
    even though the canonical helper is never invoked.
    """
    func = call.func
    if isinstance(func, ast.Name):
        if shadowed:
            return False
        return imports.get(func.id) == _PARSE_TYPED_FQN
    if isinstance(func, ast.Attribute) and func.attr == "parse_typed":
        root = func.value
        if isinstance(root, ast.Name):
            return imports.get(root.id) == _BOUNDARY_MODULE_FQN
    return False


def _function_shadows_parse_typed(node: ast.AST) -> bool:
    """Return True iff the boundary function rebinds ``parse_typed``.

    A local ``def parse_typed(...)``, ``async def parse_typed(...)``,
    or ``parse_typed = ...`` (including ``AnnAssign``) at the
    function's top scope would shadow the imported helper. The check
    only inspects the function's own body, not nested scopes (those
    cannot leak back into the parent scope's name resolution).
    """
    for stmt in getattr(node, "body", []):
        if (
            isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            and stmt.name == "parse_typed"
        ):
            return True
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "parse_typed":
                    return True
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == "parse_typed"
        ):
            return True
    return False


def _calls_parse_typed(
    node: ast.AST, boundary_label: str, imports: dict[str, str]
) -> bool:
    """Return True iff the function body calls the canonical ``parse_typed``.

    Three false-positive sources are excluded:

    * A bare presence check would let a stray ``parse_typed("jwt", ...)``
      inside the WS handler green-light the ``ws.control`` registration,
      so the first positional arg must equal the registered boundary
      label literal.
    * ``ast.walk`` would happily recurse into nested ``FunctionDef`` /
      ``AsyncFunctionDef`` / ``ClassDef`` / ``Lambda`` bodies, letting a
      ``parse_typed`` call inside a nested helper satisfy the outer
      handler's contract even when the handler's own code path forgets
      to validate. Restrict traversal to the boundary function's own
      scope and stop descending when a child node introduces a new one.
    * A local shim named ``parse_typed`` (rebound at the function top
      scope) would satisfy a token-only match even though the
      canonical helper is never invoked. Resolve the callee through
      :func:`_build_import_map` and reject any local shadowing.
    """
    shadowed = _function_shadows_parse_typed(node)
    to_visit: list[ast.AST] = list(getattr(node, "body", []))
    while to_visit:
        child = to_visit.pop()
        if isinstance(
            child,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            continue
        if (
            isinstance(child, ast.Call)
            and child.args
            and _resolves_to_parse_typed(child, imports, shadowed=shadowed)
        ):
            first_arg = child.args[0]
            if (
                isinstance(first_arg, ast.Constant)
                and first_arg.value == boundary_label
            ):
                return True
        to_visit.extend(ast.iter_child_nodes(child))
    return False


def _line_has_opt_out(source_lines: list[str], lineno: int) -> bool:
    """Return True iff the line carries the per-line opt-out marker."""
    if not 1 <= lineno <= len(source_lines):
        return False
    return _OPT_OUT_MARKER in source_lines[lineno - 1]


def _check_boundary(
    rel_path: str,
    function_name: str,
    boundary_label: str,
) -> list[str]:
    """Return zero or more violation messages for one registered boundary."""
    abs_path = REPO_ROOT / rel_path
    if not abs_path.is_file():
        return [
            f"{rel_path}: registered boundary file is missing "
            f"(expected function {function_name!r} for boundary "
            f"{boundary_label!r})"
        ]
    source = abs_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(abs_path))
    except SyntaxError as exc:
        print(f"{rel_path}: failed to parse -- {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    func = _function_node(tree, function_name)
    if func is None:
        return [
            f"{rel_path}: registered boundary function "
            f"{function_name!r} not found (boundary {boundary_label!r})"
        ]

    imports = _build_import_map(tree)
    if _calls_parse_typed(func, boundary_label, imports):
        return []

    source_lines = source.splitlines()
    if _line_has_opt_out(source_lines, func.lineno):
        return []

    return [
        f"{rel_path}:{func.lineno}: function {function_name!r} no longer "
        f"calls parse_typed; the {boundary_label!r} boundary contract is "
        "broken. Either route the inbound payload through "
        "synthorg.api.boundary.parse_typed, or add a "
        f'"# {_OPT_OUT_MARKER} -- <reason>" marker on the def line.'
    ]


def main() -> int:
    """Walk every registered boundary and report regressions.

    Translates the documented exit-code matrix:

    * 0 -- every registered boundary calls ``parse_typed``.
    * 1 -- one or more registered boundaries no longer route through
           the helper.
    * 2 -- internal error: source-file syntax error (raised as
           :class:`SystemExit` from ``_check_boundary``) OR an
           ambiguous registered-function definition (raised as
           :class:`ValueError` from ``_function_node``). Both are
           workflow bugs the operator should triage; emit a single
           stderr line and exit cleanly instead of crashing with a
           traceback.
    """
    violations: list[str] = []
    try:
        for rel_path, function_name, boundary_label in _REGISTERED_BOUNDARIES:
            violations.extend(_check_boundary(rel_path, function_name, boundary_label))
    except ValueError as exc:
        print(f"check_boundary_typed: {exc}", file=sys.stderr)
        return 2
    if not violations:
        return 0
    print(
        f"{len(violations)} typed-boundary violation(s) detected:",
        file=sys.stderr,
    )
    for line in violations:
        print(f"  {line}", file=sys.stderr)
    print(
        "\nRFC #1711 requires every registered API boundary to validate "
        "via synthorg.api.boundary.parse_typed; raw dict access at "
        "these surfaces is the exact regression this gate prevents.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
