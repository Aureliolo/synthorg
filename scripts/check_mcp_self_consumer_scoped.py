"""Gate: the MCP self-consumer scopes by per-agent capabilities.

Every ELEVATED agent must NOT see the whole ~247-tool MCP surface. The
self-consumer bridge derives an agent's visible sensitive (admin) tools
from its own ``ToolPermissions.mcp_capabilities`` (plus the ambient
read/write surface and an operator broadening), never a single global
grant. If the bridge stops reading ``identity.tools.mcp_capabilities``,
per-agent scoping silently regresses to "everyone sees everything"; this
gate guards that read.

Usage:
    uv run python scripts/check_mcp_self_consumer_scoped.py

Exit codes:
    0 -- the bridge reads the per-agent capability grant.
    1 -- the per-agent grant is no longer consumed.
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

_SELF_CONSUMER_REL: Final[str] = "src/synthorg/engine/mcp_self_consumer.py"
_GRANT_ATTR: Final[str] = "mcp_capabilities"
_OWNER_ATTR: Final[str] = "tools"


def _reads_per_agent_grant(tree: ast.Module) -> bool:
    """Whether the module reads ``<identity>.tools.mcp_capabilities``.

    Returns:
        ``True`` when an ``x.tools.mcp_capabilities`` attribute chain is
        present anywhere in the module.
    """
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == _GRANT_ATTR
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == _OWNER_ATTR
        ):
            return True
    return False


def _check(repo_root: Path) -> list[str]:
    """Verify the self-consumer consumes the per-agent capability grant.

    Returns:
        A list of violation messages (empty when the read is present).
    """
    path = repo_root / _SELF_CONSUMER_REL
    _source, tree = read_and_parse(path)
    if _reads_per_agent_grant(tree):
        return []
    return [
        f"{_SELF_CONSUMER_REL}: the bridge must read "
        f"identity.{_OWNER_ATTR}.{_GRANT_ATTR} so ELEVATED agents are scoped "
        f"per-agent, not handed the whole MCP surface"
    ]


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
