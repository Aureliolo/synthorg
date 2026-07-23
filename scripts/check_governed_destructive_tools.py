"""Gate: destructive governed tools keep their guardrail and action type.

A ``GovernedConnectionTool`` that destroys or replaces upstream state (a
release that changes what is serving, as opposed to a write that opens an
issue) carries two obligations the shared pipeline cannot infer:

1. ``require_admin_guardrails`` runs as the lexically first statement of
   ``_check_preconditions``, so an unconfirmed or unattributable call is
   refused *before* the approval gate parks it for a human; and
2. the family binds its own ``_ACTION_TYPE`` rather than inheriting
   ``comms:external``.

Rule 2 is the load-bearing one. The base pinned every family to
``comms:external``, which meant an operator granting chat autonomy
(``auto_approve_actions={"comms:external"}``) silently auto-approved
anything else sharing that type. A destructive family inheriting it would
hand production deploys to a grant written for sending messages. The
action type is resolved through the class's bases, since a family
normally binds it once on its shared base.

Note what is deliberately *not* enforced: that a tool carrying a
``deploy:`` action type must be destructive. A read-only observer on a
deploy connection legitimately carries the family's action type so risk
classification stays accurate, while causing nothing; requiring the flag
there would park an approval on every status poll, which pushes agents
away from checking the release they just made.

Residual gap, stated plainly: a destructive family that simply omits
``_DESTRUCTIVE`` escapes both rules. Nothing static can infer
destructiveness from arbitrary code. What this gate guarantees is that
the flag, once set, cannot be set *hollowly*: no guardrail-free
destructive tool, and no destructive tool hiding behind the shared action
type.

Opt a genuine exception out with a trailing
``# lint-allow: governed-destructive -- <reason>`` comment on the class's
``class`` line.

Usage:
    uv run python scripts/check_governed_destructive_tools.py

Exit codes:
    0 -- every destructive governed tool is guarded.
    1 -- a guardrail or action-type binding is missing.
    2 -- configuration error (bad ``--repo-root`` or an unreadable source).
"""

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_and_parse,
    )
else:
    from scripts._gate_source import GateSourceError, read_and_parse

_TOOLS_REL: Final[str] = "src/synthorg/tools"
_MARKER: Final[str] = "lint-allow: governed-destructive"
_ALLOW_RE: Final[re.Pattern[str]] = re.compile(
    r"#.*" + re.escape(_MARKER) + r"\s*--\s*\S"
)
_DESTRUCTIVE_FLAG: Final[str] = "_DESTRUCTIVE"
_ACTION_TYPE_ATTR: Final[str] = "_ACTION_TYPE"
_GUARDRAIL_CALL: Final[str] = "require_admin_guardrails"
_PRECONDITION_FN: Final[str] = "_check_preconditions"
_SHARED_ACTION_TYPE: Final[str] = "COMMS_EXTERNAL"


def _assigned_value(node: ast.ClassDef, name: str) -> ast.expr | None:
    """Return the value a class body assigns to *name*, if any.

    Args:
        node: The class definition to inspect.
        name: The class-attribute name to look up.

    Returns:
        The assigned expression, or ``None`` when the class does not
        assign it directly.
    """
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign):
            target = stmt.target
            if isinstance(target, ast.Name) and target.id == name:
                return stmt.value
        elif isinstance(stmt, ast.Assign):
            for assigned in stmt.targets:
                if isinstance(assigned, ast.Name) and assigned.id == name:
                    return stmt.value
    return None


def _is_true(value: ast.expr | None) -> bool:
    """Return whether *value* is the literal ``True``.

    Args:
        value: The expression to test.

    Returns:
        ``True`` when the expression is the ``True`` constant.
    """
    return isinstance(value, ast.Constant) and value.value is True


def _action_type_leaf(value: ast.expr | None) -> str | None:
    """Return the ``ActionType`` member name an action-type value names.

    Args:
        value: The assigned ``_ACTION_TYPE`` expression.

    Returns:
        The enum member name (e.g. ``"DEPLOY_PRODUCTION"``), or ``None``
        when the value is not an ``ActionType.<MEMBER>.value`` reference.
    """
    node = value
    # Unwrap the trailing ``.value`` on ``ActionType.MEMBER.value``.
    if isinstance(node, ast.Attribute) and node.attr == "value":
        node = node.value
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _guardrail_is_first_statement(node: ast.ClassDef) -> bool:
    """Return whether ``_check_preconditions`` opens with the guardrail.

    Args:
        node: The class definition to inspect.

    Returns:
        ``True`` when the class defines ``_check_preconditions`` and its
        first statement (after any docstring) calls the guardrail helper.
    """
    for stmt in node.body:
        if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if stmt.name != _PRECONDITION_FN:
            continue
        body = [
            s
            for s in stmt.body
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
        ]
        if not body:
            return False
        return _calls_guardrail(body[0])
    return False


def _calls_guardrail(stmt: ast.stmt) -> bool:
    """Return whether *stmt* is a call to the guardrail helper.

    Args:
        stmt: The statement to test.

    Returns:
        ``True`` when the statement calls ``require_admin_guardrails``,
        whether the result is assigned or discarded.
    """
    value: ast.expr | None = None
    if isinstance(stmt, ast.Assign | ast.AnnAssign | ast.Expr):
        value = stmt.value
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    if isinstance(func, ast.Name):
        return func.id == _GUARDRAIL_CALL
    return isinstance(func, ast.Attribute) and func.attr == _GUARDRAIL_CALL


@dataclass(frozen=True)
class _Resolution:
    """The outcome of walking a class's bases for an action type.

    ``ambiguous`` is the fail-closed signal: a base name that several
    modules define and none of them the referencing module. Resolving it
    by guessing could attribute the wrong action type to a destructive
    tool, which is precisely the auto-approval hazard this gate exists to
    catch, so the caller reports it instead of continuing.
    """

    leaf: str | None
    ambiguous: tuple[str, ...]


def _base_names(node: ast.ClassDef) -> list[str]:
    """Return the referencable name of each of a class's bases.

    Args:
        node: The class definition.

    Returns:
        The bare names, with an attribute base reduced to its attribute.
    """
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _resolve_action_type(
    node: ast.ClassDef,
    classes: dict[str, list[tuple[str, ast.ClassDef]]],
    *,
    module: str,
) -> _Resolution:
    """Resolve a class's effective action type through its bases.

    A family normally binds ``_ACTION_TYPE`` once on its shared base, so a
    concrete tool inherits it rather than repeating it.

    Bases are looked up by bare name because an AST scan cannot follow
    imports, so a name several modules define is resolved *within the
    referencing module* first. When only other modules define it and there
    is more than one candidate, the name is reported as ambiguous rather
    than guessed: picking the wrong one could hide an unguarded
    destructive tool.

    Args:
        node: The class definition to resolve from.
        classes: Every class definition in the scanned package, keyed by
            bare name, each carrying the module that defines it.
        module: The repo-relative module defining *node*.

    Returns:
        The resolution: the enum member name when the hierarchy binds one,
        plus any base names that could not be resolved unambiguously.
    """
    seen: set[tuple[str, str]] = set()
    ambiguous: list[str] = []
    queue: list[tuple[str, ast.ClassDef]] = [(module, node)]
    while queue:
        current_module, current = queue.pop(0)
        if (current_module, current.name) in seen:
            continue
        seen.add((current_module, current.name))
        leaf = _action_type_leaf(_assigned_value(current, _ACTION_TYPE_ATTR))
        if leaf is not None:
            return _Resolution(leaf=leaf, ambiguous=())
        for name in _base_names(current):
            candidates = classes.get(name, [])
            same_module = [c for c in candidates if c[0] == current_module]
            if same_module:
                queue.extend(same_module)
            elif len(candidates) == 1:
                queue.extend(candidates)
            elif candidates:
                ambiguous.append(name)
    return _Resolution(leaf=None, ambiguous=tuple(dict.fromkeys(ambiguous)))


def _check_class(
    node: ast.ClassDef,
    *,
    rel: str,
    lines: list[str],
    classes: dict[str, list[tuple[str, ast.ClassDef]]],
) -> list[str]:
    """Return findings for one class definition.

    Args:
        node: The class definition to inspect.
        rel: The repo-relative path, for the finding message.
        lines: The module's source lines, for the opt-out marker.
        classes: Every class definition in the package, for base lookup.

    Returns:
        The findings for this class (empty when it is not destructive or
        carries the opt-out marker).
    """
    line = node.lineno
    if 1 <= line <= len(lines) and _ALLOW_RE.search(lines[line - 1]):
        return []
    if not _is_true(_assigned_value(node, _DESTRUCTIVE_FLAG)):
        return []
    resolved = _resolve_action_type(node, classes, module=rel)

    findings: list[str] = []
    if not _guardrail_is_first_statement(node):
        findings.append(
            f"{rel}:{line}: {node.name} is destructive but does not call "
            f"{_GUARDRAIL_CALL} as the first statement of {_PRECONDITION_FN}"
        )
    if resolved.ambiguous:
        names = ", ".join(resolved.ambiguous)
        findings.append(
            f"{rel}:{line}: {node.name} is destructive and its base(s) {names} "
            f"are defined in several modules, so its {_ACTION_TYPE_ATTR} cannot "
            "be resolved; rename the base or bind the action type on this class"
        )
    elif resolved.leaf is None or resolved.leaf == _SHARED_ACTION_TYPE:
        findings.append(
            f"{rel}:{line}: {node.name} is destructive but does not bind its "
            f"own {_ACTION_TYPE_ATTR}; inheriting the shared "
            f"{_SHARED_ACTION_TYPE} type would let an autonomy grant written "
            "for a tamer family auto-approve it"
        )
    return findings


def _check(root: Path) -> list[str]:
    """Return every finding across the tools package.

    Args:
        root: The repository root.

    Returns:
        The findings, in the alphabetical module order the walk sorts by.

    Raises:
        GateSourceError: When the tools package is absent (fail closed).
    """
    base = root / _TOOLS_REL
    if not base.is_dir():
        msg = f"expected tools package not found: {base}"
        raise GateSourceError(msg)
    parsed: list[tuple[str, list[str], ast.Module]] = []
    # Keyed by bare name but carrying every definition of it: a name two
    # modules define must not silently overwrite, because the resolver
    # would then attribute one module's action type to the other's class.
    classes: dict[str, list[tuple[str, ast.ClassDef]]] = {}
    for path in sorted(base.rglob("*.py")):
        text, tree = read_and_parse(path)
        rel = path.relative_to(root).as_posix()
        parsed.append((rel, text.splitlines(), tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.setdefault(node.name, []).append((rel, node))

    findings: list[str] = []
    for rel, lines, tree in parsed:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                findings.extend(
                    _check_class(node, rel=rel, lines=lines, classes=classes)
                )
    return findings


def main(argv: list[str] | None = None) -> int:
    """Scan the tools package and return the exit code.

    Args:
        argv: Command-line arguments, or ``None`` to read ``sys.argv``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"error: --repo-root is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        findings = _check(root)
    except GateSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if findings:
        print(
            "error: a destructive governed tool must enforce the confirm + "
            "reason + actor guardrail before the approval gate and bind its "
            "own action type:",
            file=sys.stderr,
        )
        for ident in findings:
            print(f"  {ident}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
