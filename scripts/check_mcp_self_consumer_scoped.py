"""Gate: the MCP self-consumer scopes by per-agent capabilities.

Every ELEVATED agent must NOT see the whole ~247-tool MCP surface. The
self-consumer bridge derives an agent's visible sensitive (admin) tools
from its own ``ToolPermissions.mcp_capabilities`` (plus the ambient
read/write surface and an operator broadening), never a single global
grant. If the bridge stops *using* ``identity.tools.mcp_capabilities``,
per-agent scoping silently regresses to "everyone sees everything".

The check is behavioural rather than a token search: the grant must
reach the capability argument of the scoper call that selects the
visible tools (``visible_tools(...)``). A read whose result is
discarded, logged, or parked in an unused local would satisfy a bare
attribute-chain search while every agent still saw the whole sensitive
surface, so the gate follows local assignments from that argument back
to the grant.

Usage:
    uv run python scripts/check_mcp_self_consumer_scoped.py

Exit codes:
    0 -- the per-agent grant feeds the scoper's capability selection.
    1 -- the grant is unread, or no longer reaches the scoper.
    2 -- configuration error (bad ``--repo-root`` or an unreadable source).
"""

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        direct_body_nodes,
        reachable_statements,
        read_and_parse,
        statement_expressions,
    )
else:
    from scripts._gate_source import (
        GateSourceError,
        direct_body_nodes,
        reachable_statements,
        read_and_parse,
        statement_expressions,
    )

_SELF_CONSUMER_REL: Final[str] = "src/synthorg/engine/mcp_self_consumer.py"
_GRANT_ATTR: Final[str] = "mcp_capabilities"
_OWNER_ATTR: Final[str] = "tools"
_SCOPER_CALL: Final[str] = "visible_tools"
_CAPABILITY_KWARG: Final[str] = "capabilities"


def _is_grant_chain(node: ast.AST) -> bool:
    """Whether the node is an ``<x>.tools.mcp_capabilities`` chain.

    Returns:
        ``True`` for the per-agent capability grant attribute chain.
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == _GRANT_ATTR
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == _OWNER_ATTR
    )


def _reads_per_agent_grant(tree: ast.Module) -> bool:
    """Whether the module reads ``<identity>.tools.mcp_capabilities``.

    Returns:
        ``True`` when the grant attribute chain appears at all.
    """
    return any(_is_grant_chain(node) for node in ast.walk(tree))


def _local_bindings(
    scope: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, list[ast.expr]]:
    """Map each locally bound name to the expressions assigned to it.

    Only this scope's own body counts: an assignment inside a nested
    closure binds that closure's local, so crediting it here would let a
    dead inner function supply the capability set the real provider no
    longer derives from the grant.

    Returns:
        A name -> assigned-expressions map for the given scope.
    """
    bindings: dict[str, list[ast.expr]] = defaultdict(list)
    for node in direct_body_nodes(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id].append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            bindings[node.target.id].append(node.value)
    return bindings


def _reaches_grant(expr: ast.expr, bindings: dict[str, list[ast.expr]]) -> bool:
    """Whether ``expr`` is data-flow reachable from the per-agent grant.

    Follows bare local names back through their assignments, so the
    canonical ``capabilities = tuple({*identity.tools.mcp_capabilities,
    ...})`` indirection still counts as the grant feeding the scoper.

    Returns:
        ``True`` when the grant chain is reachable from the expression.
    """
    pending: list[ast.expr] = [expr]
    seen: set[str] = set()
    while pending:
        for node in ast.walk(pending.pop()):
            if _is_grant_chain(node):
                return True
            if isinstance(node, ast.Name) and node.id not in seen:
                seen.add(node.id)
                pending.extend(bindings.get(node.id, ()))
    return False


def _called_name(call: ast.Call) -> str | None:
    """Return the simple/attribute name being called, if any.

    Returns:
        The function/attribute name, or ``None`` for an unusual callee.
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _capability_argument(call: ast.Call) -> ast.expr | None:
    """Return the expression passed as the scoper's capability set.

    Returns:
        The first positional argument (or the ``capabilities=`` keyword),
        or ``None`` when the call passes neither.
    """
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg == _CAPABILITY_KWARG:
            return keyword.value
    return None


def _grant_feeds_scoper(tree: ast.Module) -> bool:
    """Whether the grant reaches a ``visible_tools(...)`` capability set.

    Only reachable scoper calls count. Walking the whole function subtree
    would let a dead branch satisfy the gate: a regression that moves the
    live dispatch to an empty capability set while leaving a now-unreachable
    call that still threads the grant through is exactly the "the grant no
    longer feeds the live scoper call" case this gate exists to catch.

    Returns:
        ``True`` when at least one reachable scoper call selects tools from
        a capability set derived from the per-agent grant.
    """
    for scope in ast.walk(tree):
        if not isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        bindings = _local_bindings(scope)
        for stmt in reachable_statements(scope.body):
            for node in statement_expressions(stmt):
                if not isinstance(node, ast.Call):
                    continue
                if _called_name(node) != _SCOPER_CALL:
                    continue
                argument = _capability_argument(node)
                if argument is not None and _reaches_grant(argument, bindings):
                    return True
    return False


def _check(repo_root: Path) -> list[str]:
    """Verify the self-consumer scopes visibility by the per-agent grant.

    Returns:
        A list of violation messages (empty when the wiring is intact).
    """
    path = repo_root / _SELF_CONSUMER_REL
    _source, tree = read_and_parse(path)
    if not _reads_per_agent_grant(tree):
        return [
            f"{_SELF_CONSUMER_REL}: the bridge must read "
            f"identity.{_OWNER_ATTR}.{_GRANT_ATTR} so ELEVATED agents are "
            f"scoped per-agent, not handed the whole MCP surface"
        ]
    if not _grant_feeds_scoper(tree):
        return [
            f"{_SELF_CONSUMER_REL}: identity.{_OWNER_ATTR}.{_GRANT_ATTR} is "
            f"read but never reaches the capability set passed to "
            f"{_SCOPER_CALL}(...), so per-agent scoping does not select the "
            f"visible sensitive tools"
        ]
    return []


def main() -> int:
    """Run the MCP self-consumer scoping gate.

    Returns:
        The process exit code (0 clean, 1 regression, 2 config error).
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
        print("MCP self-consumer scoping check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
