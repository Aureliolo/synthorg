"""Gate: MCPServerConfig keeps its npm version-pin validator.

The catalog installer pins every npm package to ``@<version>``, but a
hand-authored ``MCPServerConfig`` bypasses that path. Without a
model-level pin, an ``npx``-launched stdio server with an unpinned (or
``@latest``) package resolves whatever is newest on every reconnect, so
an un-reviewed version could start running under an agent's tools with no
config change. ``MCPServerConfig._validate_npm_pin`` closes that gap at
the model boundary; this gate guards it against silent removal.

Usage:
    uv run python scripts/check_mcp_server_config_pinned.py

Exit codes:
    0 -- the pin validator is present.
    1 -- the pin validator is missing.
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
        read_and_parse,
    )
else:
    from scripts._gate_source import GateSourceError, read_and_parse

_CONFIG_REL: Final[str] = "src/synthorg/tools/mcp/config.py"
_MODEL: Final[str] = "MCPServerConfig"
_VALIDATOR: Final[str] = "_validate_npm_pin"


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
        if name == "model_validator":
            return True
    return False


def _check(repo_root: Path) -> list[str]:
    """Verify the pin validator exists on ``MCPServerConfig``.

    Returns:
        A list of violation messages (empty when the validator is present).
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
                return []
    return [
        f"{_CONFIG_REL}: {_MODEL} must keep the {_VALIDATOR} model_validator that "
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
