"""Gate: MCPServerConfig keeps its npm version-pin validator.

The catalog installer pins every npm package to ``@<version>``, but a
hand-authored ``MCPServerConfig`` bypasses that path. Without a
model-level pin, an ``npx``-launched stdio server with an unpinned (or
``@latest``) package resolves whatever is newest on every reconnect, so
an un-reviewed version could start running under an agent's tools with no
config change. ``MCPServerConfig._validate_npm_pin`` closes that gap at
the model boundary; this gate guards it against silent removal *and*
against being hollowed out into a no-op.

Presence alone proves nothing: a validator reduced to ``return self``,
one that never inspects the launch command, or one whose rejection sits
after an unconditional ``return`` all keep the decorator and the name
while every unpinned package launches. The gate therefore requires the
validator to (1) read the launch ``command`` and its ``args``, (2) accept
conditionally (a guarded early return), and (3) keep a *reachable*
``raise ValueError`` rejection.

Usage:
    uv run python scripts/check_mcp_server_config_pinned.py

Exit codes:
    0 -- the pin validator is present and still enforces.
    1 -- the pin validator is missing or no longer rejects.
    2 -- configuration error (bad ``--repo-root`` or an unreadable source).
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        reachable_statements,
        read_and_parse,
    )
else:
    from scripts._gate_source import (
        GateSourceError,
        reachable_statements,
        read_and_parse,
    )

_CONFIG_REL: Final[str] = "src/synthorg/tools/mcp/config.py"
_MODEL: Final[str] = "MCPServerConfig"
_VALIDATOR: Final[str] = "_validate_npm_pin"
_DECORATOR: Final[str] = "model_validator"
_REJECTION: Final[str] = "ValueError"
# The launch surface the validator must inspect: the executable and the
# package spec that rides in its arguments (``npx -y pkg@1.2.3``).
_INSPECTED_FIELDS: Final[tuple[str, ...]] = ("command", "args")


def _has_model_validator(method: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Whether the method is decorated with ``@model_validator``.

    Returns:
        ``True`` when a ``model_validator`` decorator is present.
    """
    for dec in method.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = (
            target.attr
            if isinstance(target, ast.Attribute)
            else target.id
            if isinstance(target, ast.Name)
            else ""
        )
        if name == _DECORATOR:
            return True
    return False


def _reads_self_field(method: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return the ``self.<field>`` names the validator reachably reads.

    Returns:
        The set of model field names read from reachable statements.
    """
    read: set[str] = set()
    for stmt in reachable_statements(method.body):
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                read.add(node.attr)
    return read


def _accepts_conditionally(method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the validator returns from inside a branch.

    An unconditional ``return self`` is the no-op shape: it accepts every
    config, pinned or not.

    Returns:
        ``True`` when at least one ``return`` sits inside a conditional.
    """
    for stmt in reachable_statements(method.body):
        if isinstance(stmt, ast.If | ast.For | ast.While) and any(
            isinstance(inner, ast.Return) for inner in ast.walk(stmt)
        ):
            return True
    return False


def _rejects(method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether a reachable statement raises the rejection error.

    Returns:
        ``True`` when a reachable ``raise ValueError(...)`` is present.
    """
    for stmt in reachable_statements(method.body):
        if not isinstance(stmt, ast.Raise) or stmt.exc is None:
            continue
        exc = stmt.exc
        node = exc.func if isinstance(exc, ast.Call) else exc
        name = (
            node.attr
            if isinstance(node, ast.Attribute)
            else node.id
            if isinstance(node, ast.Name)
            else ""
        )
        if name == _REJECTION:
            return True
    return False


def _enforcement_violations(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    """Check the validator body still enforces the pin.

    Returns:
        A list of violation messages (empty when enforcement is intact).
    """
    violations: list[str] = []
    missing = [f for f in _INSPECTED_FIELDS if f not in _reads_self_field(method)]
    if missing:
        violations.append(
            f"{_CONFIG_REL}: {_VALIDATOR} no longer reads "
            f"{', '.join(f'self.{field}' for field in missing)}, so it cannot "
            f"see the package spec it is meant to pin"
        )
    if not _accepts_conditionally(method):
        violations.append(
            f"{_CONFIG_REL}: {_VALIDATOR} returns unconditionally, so every "
            f"config is accepted regardless of its package spec"
        )
    if not _rejects(method):
        violations.append(
            f"{_CONFIG_REL}: {_VALIDATOR} has no reachable "
            f"raise {_REJECTION}, so an unpinned npm package is never rejected"
        )
    return violations


def _check(repo_root: Path) -> list[str]:
    """Verify the pin validator exists and still rejects unpinned packages.

    Returns:
        A list of violation messages (empty when enforcement is intact).
    """
    path = repo_root / _CONFIG_REL
    _source, tree = read_and_parse(path)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == _MODEL):
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
                and stmt.name == _VALIDATOR
                and _has_model_validator(stmt)
            ):
                return _enforcement_violations(stmt)
    return [
        f"{_CONFIG_REL}: {_MODEL} must keep the {_VALIDATOR} {_DECORATOR} that "
        f"rejects an unpinned npm package (npx supply-chain pin)"
    ]


def main() -> int:
    """Run the MCP npm-pin gate.

    Returns:
        The process exit code (0 clean, 1 missing, 2 config error).
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
        print("MCP npm-pin validator check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
