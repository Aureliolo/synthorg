"""Gate: the LLM gateway binds ``(provider, model)`` from the signed token.

The gateway ([docs/design/llm-gateway.md]) is the one HTTP surface that
fronts the in-process provider registry, so Explicit Provider Binding must
hold there too: the dispatched ``(provider, model)`` pair comes from the
verified token claims, never the OpenAI request's ``model`` field, and a
provider is never auto-picked. This gate AST-scans
``src/synthorg/api/gateway/`` and fails on any of:

1. ``<registry>.list_providers()[0]`` / any ``resolve_for_model`` reference,
   direct or aliased import -- the removed provider auto-pick idioms (mirrors
   ``check_no_provider_auto_pick.py`` for the gateway module).
2. A provider lookup ``<registry>.get(<x>.model)`` or ``.get(<x>["model"])``
   -- resolving the provider from the request's ``model`` field.
3. A ``.complete(...)`` / ``.stream(...)`` dispatch whose model argument is a
   plain ``.model`` attribute (the request field) rather than the
   token-bound ``.model_id``.

It also enforces the positive contract: ``service.py`` must resolve its
provider from ``claims.provider`` at the actual ``get(...)`` lookup (tracking
the value through a helper parameter) AND bind its model from ``.model_id`` at
EVERY ``complete`` / ``stream`` dispatch, so neither an unrelated (e.g. logging)
``.provider`` read nor a second dispatch on a hard-coded model can masquerade as
the binding.

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
from collections.abc import Iterator
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        direct_body_nodes,
        reaching_alias_names,
        read_and_parse,
    )
else:
    from scripts._gate_source import (
        GateSourceError,
        direct_body_nodes,
        reaching_alias_names,
        read_and_parse,
    )

_PKG_REL: Final[str] = "src/synthorg/api/gateway"
_SERVICE_REL: Final[str] = "service.py"
_MARKER: Final[str] = "lint-allow: gateway-binding"
_ALLOW_RE: Final[re.Pattern[str]] = re.compile(
    r"#.*" + re.escape(_MARKER) + r"\s*--\s*\S"
)
_REQUEST_MODEL_ATTR: Final[str] = "model"
_BOUND_MODEL_ATTR: Final[str] = "model_id"
_PROVIDER_ATTR: Final[str] = "provider"
_CLAIMS_TYPE: Final[str] = "GatewayTokenClaims"
_VERIFY_METHOD: Final[str] = "verify"
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


def _iter_scopes(
    tree: ast.Module,
) -> Iterator[ast.Module | ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield the module scope followed by every function scope in *tree*.

    Each scope is analysed against only its own local aliases, so a name bound
    in one handler cannot satisfy a dispatch in another.

    Yields:
        The module node, then each (possibly nested) function definition node.
    """
    yield tree
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _is_alias(node: ast.expr | None, aliases: frozenset[str]) -> bool:
    """Whether *node* is a bare name bound to a tracked alias.

    Returns:
        ``True`` when *node* is an ``ast.Name`` whose id is in *aliases*.
    """
    return isinstance(node, ast.Name) and node.id in aliases


def _binds_request_model(
    arg: ast.expr | None, kw: ast.expr | None, aliases: frozenset[str]
) -> bool:
    """Whether a call's model argument reads (or aliases) the request model.

    Returns:
        ``True`` for a direct ``.model`` / ``["model"]`` read or a bare name
        bound to one in the enclosing scope.
    """
    return (
        _is_request_model(arg)
        or _is_request_model(kw)
        or _is_alias(arg, aliases)
        or _is_alias(kw, aliases)
    )


def _scan_module(tree: ast.Module, lines: list[str], relpath: str) -> list[str]:
    """Return every request-model-binding / auto-pick finding in one module."""
    findings: list[str] = []

    def _allowed(lineno: int) -> bool:
        return 1 <= lineno <= len(lines) and bool(_ALLOW_RE.search(lines[lineno - 1]))

    def _record(lineno: int, code: str) -> None:
        if not _allowed(lineno):
            findings.append(f"{relpath}:{lineno}:{code}")

    # Module-wide auto-pick idioms: any removed-resolver import / name /
    # attribute reference is a violation regardless of scope.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _REMOVED_RESOLVER:
                    _record(node.lineno, f"import of {_REMOVED_RESOLVER}")
        elif (isinstance(node, ast.Name) and node.id == _REMOVED_RESOLVER) or (
            isinstance(node, ast.Attribute) and node.attr == _REMOVED_RESOLVER
        ):
            _record(node.lineno, _REMOVED_RESOLVER)

    # Provider lookups and dispatches are screened per lexical scope so a
    # request-model alias bound in one handler cannot satisfy a call in another.
    for scope in _iter_scopes(tree):
        aliases = reaching_alias_names(scope, _is_request_model)
        for node in direct_body_nodes(scope):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            attr = node.func.attr
            if attr in _RESOLVE_CALLS and _binds_request_model(
                node.args[0] if node.args else None, _model_keyword(node), aliases
            ):
                _record(node.lineno, "provider-from-request-model")
            elif attr in _DISPATCH_CALLS and _binds_request_model(
                _second_positional(node), _model_keyword(node), aliases
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


def _is_verify_call(node: ast.expr | None) -> bool:
    """Whether *node* is a ``<signer>.verify(...)`` call yielding token claims.

    Returns:
        ``True`` for a call whose callee attribute is ``verify``.
    """
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == _VERIFY_METHOD
    )


def _claims_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return *fn* parameter names annotated as ``GatewayTokenClaims``.

    Returns:
        The claims-typed parameter identifiers (any annotation mentioning the
        claims type, so ``GatewayTokenClaims`` and ``GatewayTokenClaims | None``
        both qualify).
    """
    args = fn.args
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    return {
        arg.arg
        for arg in params
        if arg.annotation is not None
        and any(
            isinstance(n, ast.Name) and n.id == _CLAIMS_TYPE
            for n in ast.walk(arg.annotation)
        )
    }


def _claims_base_names(tree: ast.Module) -> set[str]:
    """Return local names bound to the verified gateway token claims.

    A name qualifies when it is assigned from a ``<signer>.verify(...)`` call or
    is a function parameter annotated ``GatewayTokenClaims`` -- the two ways the
    service obtains the token-claims object it must bind from.

    Returns:
        The set of claims-carrying local identifiers.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_verify_call(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and _is_verify_call(node.value)
        ):
            names.add(node.target.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.update(_claims_params(node))
    return names


def _is_bound_model_attr(node: ast.expr | None) -> bool:
    """Whether *node* reads the token-bound ``.model_id`` attribute.

    Returns:
        ``True`` for a ``<x>.model_id`` attribute access.
    """
    return isinstance(node, ast.Attribute) and node.attr == _BOUND_MODEL_ATTR


def _dispatch_calls(tree: ast.Module) -> list[tuple[ast.Call, frozenset[str]]]:
    """Return every ``complete`` / ``stream`` dispatch call in *tree*.

    Each call is paired with the ``.model_id`` aliases of its enclosing lexical
    scope, so a per-call bound-model check can honour a scope-local alias.

    Returns:
        ``(call, bound_model_aliases)`` pairs for each dispatch found.
    """
    calls: list[tuple[ast.Call, frozenset[str]]] = []
    for scope in _iter_scopes(tree):
        bound = reaching_alias_names(scope, _is_bound_model_attr)
        calls.extend(
            (node, bound)
            for node in direct_body_nodes(scope)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _DISPATCH_CALLS
        )
    return calls


def _unbound_dispatch_lines(tree: ast.Module) -> list[int]:
    """Return the line of each dispatch whose model is not claims-bound.

    Every gateway dispatch must pass ``.model_id`` (or a scope-local alias of it)
    as its model argument; a hard-coded or otherwise token-unbound model is a
    violation even though it is not the request's ``model`` field, so checking
    only one compliant dispatch would let a second, unbound one slip through.

    Returns:
        The (sorted) line numbers of dispatches that bind an unbound model.
    """
    lines = [
        node.lineno
        for node, bound in _dispatch_calls(tree)
        if not (
            _is_bound_model_attr(_second_positional(node) or _model_keyword(node))
            or _is_alias(_second_positional(node) or _model_keyword(node), bound)
        )
    ]
    return sorted(lines)


def _is_claims_provider(node: ast.expr | None, claims_names: frozenset[str]) -> bool:
    """Whether *node* reads ``.provider`` off a verified-claims base.

    Returns:
        ``True`` for a ``<claims>.provider`` attribute access whose base is a
        known claims identifier.
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == _PROVIDER_ATTR
        and isinstance(node.value, ast.Name)
        and node.value.id in claims_names
    )


def _callee_name(func: ast.expr) -> str | None:
    """Return the called name of *func* (``name`` or ``<x>.name``), if any.

    Returns:
        The callee identifier, or ``None`` for a non-name callable.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _positional_param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return *fn*'s positional parameter names, dropping an implicit receiver.

    A ``@staticmethod`` keeps every parameter; otherwise a leading ``self`` /
    ``cls`` is dropped so call arguments line up with parameters by position
    regardless of method vs. plain-function call style.

    Returns:
        The positional parameter names in declaration order.
    """
    names = [a.arg for a in (*fn.args.posonlyargs, *fn.args.args)]
    is_static = any(
        isinstance(d, ast.Name) and d.id == "staticmethod" for d in fn.decorator_list
    )
    if not is_static and names and names[0] in {"self", "cls"}:
        return names[1:]
    return names


# A call mapped to the function whose own body contains it (``None`` = module).
_EnclosingMap = dict[ast.Call, "ast.FunctionDef | ast.AsyncFunctionDef | None"]
# Per-scope claims-provider alias sets, keyed by scope node (``None`` = module).
_AliasCache = dict["ast.AST | None", frozenset[str]]
# Per-function parameter names proven to carry the claims provider.
_ProvenParams = dict["ast.FunctionDef | ast.AsyncFunctionDef", set[str]]


def _enclosing_functions(tree: ast.Module) -> _EnclosingMap:
    """Map each call in *tree* to its nearest enclosing function.

    Returns:
        A mapping from each ``ast.Call`` to the function whose own body contains
        it, or ``None`` for a module-level call.
    """
    enclosing: _EnclosingMap = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in direct_body_nodes(fn):
                if isinstance(node, ast.Call):
                    enclosing[node] = fn
    for node in direct_body_nodes(tree):
        if isinstance(node, ast.Call) and node not in enclosing:
            enclosing[node] = None
    return enclosing


def _is_claims_provider_value(
    expr: ast.expr | None,
    scope: ast.FunctionDef | ast.AsyncFunctionDef | None,
    claims_names: frozenset[str],
    alias_cache: _AliasCache,
    proven: _ProvenParams,
) -> bool:
    """Whether *expr* provably carries ``claims.provider`` on *scope*'s path.

    True for a direct ``claims.provider`` read, a scope-local alias singly and
    unconditionally bound to it, or a parameter of *scope* proven to receive the
    claims provider at every call site.

    Returns:
        ``True`` when *expr* provably carries the claims provider in *scope*.
    """
    if _is_claims_provider(expr, claims_names):
        return True
    if not isinstance(expr, ast.Name):
        return False
    if expr.id in alias_cache.get(scope, frozenset()):
        return True
    return scope is not None and expr.id in proven.get(scope, set())


def _call_arg_carries_provider(
    call: ast.Call,
    index: int,
    *,
    enclosing: _EnclosingMap,
    claims_names: frozenset[str],
    alias_cache: _AliasCache,
    proven: _ProvenParams,
) -> bool:
    """Whether *call*'s positional argument at *index* carries claims.provider.

    Evaluated in the caller's own scope, so a caller alias or a caller parameter
    already proven to carry the claims provider counts.

    Returns:
        ``True`` when the positional argument provably carries the claims provider.
    """
    pos_args = [a for a in call.args if not isinstance(a, ast.Starred)]
    if index >= len(pos_args):
        return False
    return _is_claims_provider_value(
        pos_args[index], enclosing.get(call), claims_names, alias_cache, proven
    )


def _prove_provider_flow(
    tree: ast.Module, claims_names: frozenset[str]
) -> tuple[_ProvenParams, _AliasCache, _EnclosingMap]:
    """Prove, per function, which parameters always carry ``claims.provider``.

    A parameter is proven only when EVERY call to its function passes a
    claims-provider value in that position (directly, via a caller alias, or via
    a caller's own proven parameter), computed to a fixpoint so the flow chains
    across helpers. A single call passing unrelated input disqualifies it, so a
    parameter cannot borrow trust from an unrelated scope or a call carrying
    request-controlled input.

    Returns:
        The per-function proven-parameter sets, the per-scope alias cache, and
        the call-to-enclosing-function map (all reused by the caller).
    """

    def _reads(value: ast.expr | None) -> bool:
        return _is_claims_provider(value, claims_names)

    functions = [
        f
        for f in ast.walk(tree)
        if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    alias_cache: _AliasCache = {None: reaching_alias_names(tree, _reads)}
    for fn in functions:
        alias_cache[fn] = reaching_alias_names(fn, _reads)

    by_name = {fn.name: fn for fn in functions}
    enclosing = _enclosing_functions(tree)
    calls_by_fn: dict[ast.FunctionDef | ast.AsyncFunctionDef, list[ast.Call]] = {}
    for call in enclosing:
        name = _callee_name(call.func)
        target = by_name.get(name) if name is not None else None
        if target is not None:
            calls_by_fn.setdefault(target, []).append(call)

    proven: _ProvenParams = {fn: set() for fn in functions}
    changed = True
    while changed:
        changed = False
        for fn, calls in calls_by_fn.items():
            for index, pname in enumerate(_positional_param_names(fn)):
                if pname in proven[fn]:
                    continue
                if all(
                    _call_arg_carries_provider(
                        call,
                        index,
                        enclosing=enclosing,
                        claims_names=claims_names,
                        alias_cache=alias_cache,
                        proven=proven,
                    )
                    for call in calls
                ):
                    proven[fn].add(pname)
                    changed = True
    return proven, alias_cache, enclosing


def _resolve_binds_provider(tree: ast.Module, claims_names: frozenset[str]) -> bool:
    """Whether a provider lookup binds its argument from ``claims.provider``.

    Ties the positive contract to an actual ``get(...)`` call whose provider
    argument provably carries ``claims.provider`` in the lookup's own scope --
    directly, via a scope-local alias, or via a parameter proven to receive the
    claims provider at every call site -- so neither a logging-only read nor a
    cross-scope name collision can satisfy the binding.

    Returns:
        ``True`` when a claims-bound provider lookup is present.
    """
    proven, alias_cache, enclosing = _prove_provider_flow(tree, claims_names)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _RESOLVE_CALLS
        ):
            arg = node.args[0] if node.args else None
            if _is_claims_provider_value(
                arg, enclosing.get(node), claims_names, alias_cache, proven
            ):
                return True
    return False


def _service_binds_token(root: Path) -> str | None:
    """Return an error string when ``service.py`` drops token binding.

    Requires a provider lookup to bind ``claims.provider`` AND EVERY dispatch to
    bind its model from ``.model_id``, so neither an unrelated (e.g. logging)
    ``.provider`` read, nor a dispatch on a request-derived or hard-coded model,
    can masquerade as the binding.

    Returns:
        ``None`` when the provider and model bindings both hold; otherwise a
        description of the missing binding.
    """
    service = root / _PKG_REL / _SERVICE_REL
    if not service.is_file():
        return f"gateway service module missing: {service}"
    _, tree = read_and_parse(service)
    claims_names = frozenset(_claims_base_names(tree))
    if not _resolve_binds_provider(tree, claims_names):
        return (
            f"{_PKG_REL}/{_SERVICE_REL} does not resolve its provider from "
            f".{_PROVIDER_ATTR} of the verified token claims "
            "(Explicit Provider Binding regressed)"
        )
    if not _dispatch_calls(tree):
        return (
            f"{_PKG_REL}/{_SERVICE_REL} has no dispatch binding its model from "
            f".{_BOUND_MODEL_ATTR} (Explicit Provider Binding regressed)"
        )
    unbound = _unbound_dispatch_lines(tree)
    if unbound:
        joined = ", ".join(str(line) for line in unbound)
        return (
            f"{_PKG_REL}/{_SERVICE_REL} dispatch(es) at line(s) {joined} do not "
            f"bind their model from .{_BOUND_MODEL_ATTR} of the token claims "
            "(Explicit Provider Binding regressed)"
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
