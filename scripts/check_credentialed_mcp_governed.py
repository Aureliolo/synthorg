"""Gate: the credentialed-MCP invoke path stays governed and SEC-1-fenced.

The credentialed-tool MCP server ([docs/design/credentialed-mcp.md]) exposes
credential-holding forge / chat tools to an embedded harness. Its single
invoke path (``invoke_credentialed_tool``) must keep every governance step,
so this gate AST-scans ``src/synthorg/api/mcp_gateway/tools.py`` and fails
unless the invoke function:

1. scopes visibility per actor (``visible_tool_names``),
2. consults the SecOps pre-tool screen (``security_pre_check``),
3. validates arguments at the typed boundary (``parse_typed``),
4. dispatches through the governed tool (``.execute``), and
5. fences the returned result with ``wrap_untrusted`` (SEC-1 at source).

The module must also reference ``TAG_TOOL_RESULT`` so the fence tag cannot
drift to an unfenced return. Any missing step means credentials or untrusted
tool output could reach the harness ungoverned.

Opt a genuine exception out with a trailing
``# lint-allow: credentialed-mcp-governed -- <reason>`` comment on the
function's ``def`` line.

Usage:
    uv run python scripts/check_credentialed_mcp_governed.py

Exit codes:
    0 -- the invoke path is fully governed.
    1 -- a governance step is missing.
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
        read_and_parse,
    )
else:
    from scripts._gate_source import GateSourceError, read_and_parse

_TOOLS_REL: Final[str] = "src/synthorg/api/mcp_gateway/tools.py"
_INVOKE_FN: Final[str] = "invoke_credentialed_tool"
_MARKER: Final[str] = "lint-allow: credentialed-mcp-governed"
_ALLOW_RE: Final[re.Pattern[str]] = re.compile(
    r"#.*" + re.escape(_MARKER) + r"\s*--\s*\S"
)
_REQUIRED_CALLS: Final[tuple[str, ...]] = (
    "visible_tool_names",
    "security_pre_check",
    "parse_typed",
    "execute",
    "wrap_untrusted",
)
_REQUIRED_NAMES: Final[tuple[str, ...]] = ("TAG_TOOL_RESULT",)


def _called_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return the set of function/method names called within *fn*.

    Returns:
        Every ``name(...)`` and ``<x>.name(...)`` callee name in the body.
    """
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _find_invoke(
    tree: ast.Module,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the invoke function node, or ``None`` when absent.

    Returns:
        The ``invoke_credentialed_tool`` definition, or ``None``.
    """
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == _INVOKE_FN
        ):
            return node
    return None


def _check(root: Path) -> list[str]:
    """Return every missing-governance finding for the invoke path.

    Raises:
        GateSourceError: When the tools module is absent (fail closed).
    """
    path = root / _TOOLS_REL
    if not path.is_file():
        msg = f"expected credentialed-MCP tools module not found: {path}"
        raise GateSourceError(msg)
    text, tree = read_and_parse(path)
    lines = text.splitlines()

    module_names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    findings = [
        f"{_TOOLS_REL}: module never references {name} (SEC-1 fence tag)"
        for name in _REQUIRED_NAMES
        if name not in module_names
    ]

    invoke = _find_invoke(tree)
    if invoke is None:
        findings.append(f"{_TOOLS_REL}: {_INVOKE_FN} not found")
        return findings

    line = invoke.lineno
    if 1 <= line <= len(lines) and _ALLOW_RE.search(lines[line - 1]):
        return findings

    called = _called_names(invoke)
    findings.extend(
        f"{_TOOLS_REL}:{line}: {_INVOKE_FN} omits governed step {call!r}"
        for call in _REQUIRED_CALLS
        if call not in called
    )
    return findings


def main(argv: list[str] | None = None) -> int:
    """Scan the credentialed-MCP invoke path and return the exit code."""
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
            "error: the credentialed-MCP invoke path must scope, validate, "
            "dispatch through the governed tool, and wrap_untrusted its output:",
            file=sys.stderr,
        )
        for ident in findings:
            print(f"  {ident}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
