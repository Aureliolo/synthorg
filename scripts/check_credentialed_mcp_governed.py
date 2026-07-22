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

The ``wrap_untrusted`` fence in the invoke body must pass ``TAG_TOOL_RESULT``
as its tag argument, so the fence cannot drift to a different tag while a stray
``TAG_TOOL_RESULT`` reference elsewhere masks the regression. Any missing step
means credentials or untrusted tool output could reach the harness ungoverned.

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
        direct_body_nodes,
        read_and_parse,
    )
else:
    from scripts._gate_source import (
        GateSourceError,
        direct_body_nodes,
        read_and_parse,
    )

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
)
_FENCE_CALL: Final[str] = "wrap_untrusted"
_RESULT_TAG: Final[str] = "TAG_TOOL_RESULT"


def _called_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return the set of function/method names called directly within *fn*.

    Returns:
        Every ``name(...)`` and ``<x>.name(...)`` callee name in *fn*'s own
        body, excluding calls inside nested definition scopes.
    """
    names: set[str] = set()
    for node in direct_body_nodes(fn):
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


def _keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    """Return the value of keyword *name* on *call*, if present.

    Returns:
        The keyword argument value, or ``None``.
    """
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _is_result_tag(node: ast.expr | None) -> bool:
    """Whether *node* names the SEC-1 result tag ``TAG_TOOL_RESULT``.

    Returns:
        ``True`` for a bare ``TAG_TOOL_RESULT`` name or a ``*.TAG_TOOL_RESULT``
        attribute access.
    """
    if isinstance(node, ast.Name):
        return node.id == _RESULT_TAG
    return isinstance(node, ast.Attribute) and node.attr == _RESULT_TAG


def _fences_with_result_tag(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether *fn* fences a result with ``wrap_untrusted(TAG_TOOL_RESULT, ...)``.

    The tag must be the first positional argument (or ``tag=`` keyword) of a
    ``wrap_untrusted`` call in *fn*'s own body; a call under any other tag, or
    one buried in a nested helper scope, fails the SEC-1 boundary even if
    ``TAG_TOOL_RESULT`` is referenced elsewhere.

    Returns:
        ``True`` when a correctly-tagged fence call is present.
    """
    for node in direct_body_nodes(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            callee = func.id
        elif isinstance(func, ast.Attribute):
            callee = func.attr
        else:
            continue
        if callee != _FENCE_CALL:
            continue
        tag = node.args[0] if node.args else _keyword_value(node, "tag")
        if _is_result_tag(tag):
            return True
    return False


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

    invoke = _find_invoke(tree)
    if invoke is None:
        return [f"{_TOOLS_REL}: {_INVOKE_FN} not found"]

    line = invoke.lineno
    if 1 <= line <= len(lines) and _ALLOW_RE.search(lines[line - 1]):
        return []

    called = _called_names(invoke)
    findings = [
        f"{_TOOLS_REL}:{line}: {_INVOKE_FN} omits governed step {call!r}"
        for call in _REQUIRED_CALLS
        if call not in called
    ]
    if not _fences_with_result_tag(invoke):
        findings.append(
            f"{_TOOLS_REL}:{line}: {_INVOKE_FN} must fence its result with "
            f"{_FENCE_CALL}({_RESULT_TAG}, ...)"
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
