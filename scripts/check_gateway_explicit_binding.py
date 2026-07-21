"""Gate: the LLM gateway binds ``(provider, model)`` from the signed token.

The gateway ([docs/design/llm-gateway.md]) is the one HTTP surface that
fronts the in-process provider registry, so Explicit Provider Binding must
hold there too: the dispatched ``(provider, model)`` pair comes from the
verified token claims, never the OpenAI request's ``model`` field, and a
provider is never auto-picked. This gate AST-scans
``src/synthorg/api/gateway/`` and fails on any of:

1. ``<registry>.list_providers()[0]`` / a ``resolve_for_model`` reference
   -- the removed provider auto-pick idioms (mirrors
   ``check_no_provider_auto_pick.py`` for the gateway module).
2. A provider lookup ``<registry>.get(<x>.model)`` or ``.get(<x>["model"])``
   -- resolving the provider from the request's ``model`` field.
3. A ``.complete(...)`` / ``.stream(...)`` dispatch whose model argument is a
   plain ``.model`` attribute (the request field) rather than the
   token-bound ``.model_id``.

It also enforces the positive contract: ``service.py`` must read both
``.provider`` and ``.model_id`` from the token claims, so a refactor that
drops token binding entirely cannot pass silently.

Opt a genuine exception out with a trailing
``# lint-allow: gateway-binding -- <reason>`` comment on the offending line.

Usage:
    uv run python scripts/check_gateway_explicit_binding.py

Exit codes:
    0 -- the gateway binds from the token.
    1 -- a request-model binding or provider auto-pick was found.
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

_PKG_REL: Final[str] = "src/synthorg/api/gateway"
_SERVICE_REL: Final[str] = "service.py"
_MARKER: Final[str] = "lint-allow: gateway-binding"
_ALLOW_RE: Final[re.Pattern[str]] = re.compile(
    r"#.*" + re.escape(_MARKER) + r"\s*--\s*\S"
)
_REQUEST_MODEL_ATTR: Final[str] = "model"
_BOUND_MODEL_ATTR: Final[str] = "model_id"
_RESOLVE_CALLS: Final[frozenset[str]] = frozenset({"get"})
_DISPATCH_CALLS: Final[frozenset[str]] = frozenset({"complete", "stream"})
_REMOVED_RESOLVER: Final[str] = "resolve_for_model"
_LIST_PROVIDERS: Final[str] = "list_providers"
_MODEL_ARG_POSITION: Final[int] = 2


def _is_request_model(node: ast.expr | None) -> bool:
    """Whether *node* reads the OpenAI request's ``model`` field.

    Matches ``<x>.model`` (but not ``<x>.model_id``) and ``<x>["model"]``.

    Returns:
        ``True`` when the node reads the untrusted request model field.
    """
    if isinstance(node, ast.Attribute):
        return node.attr == _REQUEST_MODEL_ATTR
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == _REQUEST_MODEL_ATTR
    )


def _second_positional(call: ast.Call) -> ast.expr | None:
    """Return the 2nd positional argument of *call*, if any.

    Returns:
        The model-position argument, or ``None``.
    """
    positional = [a for a in call.args if not isinstance(a, ast.Starred)]
    if len(positional) >= _MODEL_ARG_POSITION:
        return positional[_MODEL_ARG_POSITION - 1]
    return None


def _model_keyword(call: ast.Call) -> ast.expr | None:
    """Return the ``model=`` keyword argument value of *call*, if any.

    A dispatch / lookup that binds the model by keyword (``.get(model=...)``,
    ``.complete(..., model=...)``) must be screened by the same rule as the
    positional form, or the explicit-binding contract is bypassed by call
    style.

    Returns:
        The ``model=`` keyword value, or ``None``.
    """
    for keyword in call.keywords:
        if keyword.arg == _REQUEST_MODEL_ATTR:
            return keyword.value
    return None


def _scan_module(tree: ast.Module, lines: list[str], relpath: str) -> list[str]:
    """Return every request-model-binding / auto-pick finding in one module."""
    findings: list[str] = []

    def _allowed(lineno: int) -> bool:
        return 1 <= lineno <= len(lines) and bool(_ALLOW_RE.search(lines[lineno - 1]))

    def _record(lineno: int, code: str) -> None:
        if not _allowed(lineno):
            findings.append(f"{relpath}:{lineno}:{code}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == _REMOVED_RESOLVER:
            _record(node.lineno, _REMOVED_RESOLVER)
            continue
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        if attr in _RESOLVE_CALLS and (
            (node.args and _is_request_model(node.args[0]))
            or _is_request_model(_model_keyword(node))
        ):
            _record(node.lineno, "provider-from-request-model")
        elif attr in _DISPATCH_CALLS and (
            _is_request_model(_second_positional(node))
            or _is_request_model(_model_keyword(node))
        ):
            _record(node.lineno, "dispatch-request-model")
    return findings


def _scan_subscripts(tree: ast.Module, lines: list[str], relpath: str) -> list[str]:
    """Flag ``<x>.list_providers()[0]`` subscripts in one module."""
    findings: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == 0
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == _LIST_PROVIDERS
        ):
            line = node.lineno
            if not (1 <= line <= len(lines) and _ALLOW_RE.search(lines[line - 1])):
                findings.append(f"{relpath}:{line}:list_providers()[0]")
    return findings


def _service_binds_token(root: Path) -> str | None:
    """Return an error string when ``service.py`` drops token binding.

    Returns:
        ``None`` when both ``.provider`` and ``.model_id`` are read from the
        claims; otherwise a description of the missing binding.
    """
    service = root / _PKG_REL / _SERVICE_REL
    if not service.is_file():
        return f"gateway service module missing: {service}"
    _, tree = read_and_parse(service)
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    missing = {"provider", _BOUND_MODEL_ATTR} - attrs
    if missing:
        return (
            f"{_PKG_REL}/{_SERVICE_REL} does not bind {sorted(missing)} from the "
            "token claims (Explicit Provider Binding regressed)"
        )
    return None


def _scan(root: Path) -> list[str]:
    """Return every gateway-binding violation under the gateway package.

    Raises:
        GateSourceError: When the gateway package is absent (fail closed).
    """
    pkg = root / _PKG_REL
    if not pkg.is_dir():
        msg = f"expected gateway package not found: {pkg}"
        raise GateSourceError(msg)
    findings: list[str] = []
    for path in sorted(pkg.rglob("*.py")):
        relpath = path.relative_to(root).as_posix()
        text, tree = read_and_parse(path)
        lines = text.splitlines()
        findings.extend(_scan_module(tree, lines, relpath))
        findings.extend(_scan_subscripts(tree, lines, relpath))
    return findings


def main(argv: list[str] | None = None) -> int:
    """Scan the gateway for request-model binding and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"error: --repo-root is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        findings = sorted(set(_scan(root)))
        binding_error = _service_binds_token(root)
    except GateSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if findings or binding_error is not None:
        print(
            "error: gateway must bind (provider, model) from the signed token, "
            "never the request's model field:",
            file=sys.stderr,
        )
        for ident in findings:
            print(f"  {ident}", file=sys.stderr)
        if binding_error is not None:
            print(f"  {binding_error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
