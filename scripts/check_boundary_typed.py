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


def _calls_parse_typed(node: ast.AST, boundary_label: str) -> bool:
    """Return True iff the function body calls ``parse_typed`` for this label.

    A bare presence check would let a stray ``parse_typed("jwt", ...)``
    inside the WS handler green-light the ``ws.control`` registration.
    Match the bare-name call AND verify the first positional argument is
    the exact boundary label literal, so each registration only accepts
    its own boundary.
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if not (isinstance(func, ast.Name) and func.id == "parse_typed"):
            continue
        if not child.args:
            continue
        first_arg = child.args[0]
        if isinstance(first_arg, ast.Constant) and first_arg.value == boundary_label:
            return True
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

    if _calls_parse_typed(func, boundary_label):
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
    """Walk every registered boundary and report regressions."""
    violations: list[str] = []
    for rel_path, function_name, boundary_label in _REGISTERED_BOUNDARIES:
        violations.extend(_check_boundary(rel_path, function_name, boundary_label))
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
