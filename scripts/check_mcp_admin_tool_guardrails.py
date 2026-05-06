#!/usr/bin/env python3
"""Pre-push gate: every MCP ``admin_tool`` handler calls the guardrail.

The MCP handler contract (``docs/reference/mcp-handler-contract.md``)
mandates: every handler whose tool is registered with
:func:`synthorg.meta.mcp.tool_builder.admin_tool` must call
:func:`synthorg.meta.mcp.handlers.common.require_admin_guardrails`
with ``arguments`` and ``actor`` as the lexically first call in its
body. The guardrail enforces an actor with an audit-usable identifier,
``arguments["confirm"] is True`` and a non-blank ``arguments["reason"]``
before any mutation runs.

The gate works in three AST passes:

1. Walk every ``src/synthorg/meta/mcp/domains/*.py`` file. Each
   ``admin_tool(domain_literal, action_literal, ...)`` call yields a
   handler key ``f"synthorg_{domain}_{action}"``. A non-literal
   ``admin_tool(domain_var, ...)`` is a hard error -- the gate refuses
   to silently miss admin tools whose registration is hidden behind
   variables.

2. For each handler key, walk every ``handlers/*.py`` file looking for
   that key in a ``*_HANDLERS = MappingProxyType({...})`` assignment.
   The dict literal may be wrapped in a ``copy.deepcopy(...)`` call
   (a common shape that immutably-snapshots the registry); the gate
   unwraps that. The value associated with the key may be:

   * a same-module ``async def`` -- inspected directly;
   * a same-module name binding (``_alias = imported_name``) where
     ``imported_name`` came from ``from .other import imported_name``
     -- followed across modules;
   * a factory call (``_make_window_handler(...)``) -- skipped because
     the closure body cannot be statically inspected; the audit covers
     these.

3. For the resolved function def, verify the lexically first
   :class:`ast.Call` reachable from the body (descending into a single
   outer ``try:`` if present) is
   ``require_admin_guardrails(arguments, actor)``. Trivial preludes
   (constant-bound assignments, the docstring) are skipped.

Per-line opt-out::

    async def _foo(  # lint-allow: mcp-admin-guardrail -- <reason>
        ...

The justification after ``--`` is required and must be non-empty.

Fail-closed: any unparseable file is a gate violation.
"""

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

# ── Paths ────────────────────────────────────────────────────────

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_MCP_ROOT: Final[Path] = _REPO_ROOT / "src" / "synthorg" / "meta" / "mcp"
_DOMAINS_ROOT: Final[Path] = _MCP_ROOT / "domains"
_HANDLERS_ROOT: Final[Path] = _MCP_ROOT / "handlers"

# ── Contract constants ───────────────────────────────────────────

_ADMIN_BUILDER_NAME: Final[str] = "admin_tool"
_GUARDRAIL_NAME: Final[str] = "require_admin_guardrails"
_GUARDRAIL_ARG_NAMES: Final[tuple[str, str]] = ("arguments", "actor")
_TOOL_NAME_PREFIX: Final[str] = "synthorg_"
_HANDLERS_PACKAGE: Final[str] = "synthorg.meta.mcp.handlers"

# ── Suppression marker ───────────────────────────────────────────

_SUPPRESSION_MARKER: Final[str] = "lint-allow: mcp-admin-guardrail"
_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"\blint-allow:\s*mcp-admin-guardrail\s*--\s*\S",
)


# ── Errors / dataclasses ─────────────────────────────────────────


class InspectionError(RuntimeError):
    """A source file could not be parsed for AST inspection."""


@dataclass(frozen=True)
class _Violation:
    """A single gate violation with stable repo-relative location."""

    rel_path: str
    lineno: int
    message: str

    def render(self) -> str:
        return f"{self.rel_path}:{self.lineno}: {self.message}"


@dataclass(frozen=True)
class _ModuleSnapshot:
    """Parsed handlers/*.py module with its symbol resolution tables."""

    path: Path
    rel_path: str
    source_lines: tuple[str, ...]
    funcs: dict[str, ast.AsyncFunctionDef | ast.FunctionDef]
    """Same-module ``async def`` / ``def`` registry, keyed by name."""
    aliases: dict[str, str]
    """``_alias = name`` bindings to a same-module name."""
    imports: dict[str, tuple[str, str]]
    """``name -> (target_module, target_name)`` from ``from x import y[as z]``."""
    handler_dicts: dict[str, dict[str, ast.expr]]
    """``*_HANDLERS`` map name -> ``{tool_key: value_expr}`` (raw value AST)."""


# ── Pass 1: discover admin handler keys (domains/) ───────────────


def _literal_str(node: ast.expr) -> str | None:
    """Return *node*'s value if it is a string ``ast.Constant``, else ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _admin_tool_key(call: ast.Call) -> str | None:
    """Return the handler key derived from an ``admin_tool(...)`` call.

    Both ``domain`` and ``action`` must be string literals (positional
    or keyword); a non-literal form is rejected by the caller.
    """
    domain: str | None = None
    action: str | None = None
    if len(call.args) >= 1:
        domain = _literal_str(call.args[0])
    if len(call.args) >= 2:  # noqa: PLR2004 -- positional indices, not magic
        action = _literal_str(call.args[1])
    for kw in call.keywords:
        if kw.arg == "domain" and domain is None:
            domain = _literal_str(kw.value)
        elif kw.arg == "action" and action is None:
            action = _literal_str(kw.value)
    if domain is None or action is None:
        return None
    return f"{_TOOL_NAME_PREFIX}{domain}_{action}"


def _scan_domains_file(path: Path) -> tuple[set[str], list[_Violation]]:
    """Return ``(handler_keys, violations)`` from a single ``domains/*.py``."""
    rel = _rel(path)
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (UnicodeDecodeError, OSError, SyntaxError) as exc:
        msg = f"failed to parse {rel}: {type(exc).__name__}: {exc}"
        raise InspectionError(msg) from exc

    keys: set[str] = set()
    violations: list[_Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != _ADMIN_BUILDER_NAME:
            continue
        key = _admin_tool_key(node)
        if key is None:
            violations.append(
                _Violation(
                    rel,
                    node.lineno,
                    "admin_tool(...) requires literal domain + action strings; "
                    "the gate cannot derive a handler key from variables.",
                ),
            )
            continue
        keys.add(key)
    return keys, violations


def _discover_admin_keys(domains_root: Path) -> tuple[set[str], list[_Violation]]:
    """Aggregate admin handler keys across every ``domains/*.py`` file.

    A parse failure on one domains file becomes a fail-closed violation
    (matching the handlers/*.py side via :func:`_build_module_snapshots`)
    rather than a raised exception, so the gate continues scanning the
    remaining files and surfaces every problem in one report.
    """
    keys: set[str] = set()
    violations: list[_Violation] = []
    for path in _iter_python_files(domains_root):
        try:
            file_keys, file_violations = _scan_domains_file(path)
        except InspectionError as exc:
            violations.append(_Violation(_rel(path), 0, str(exc)))
            continue
        keys.update(file_keys)
        violations.extend(file_violations)
    return keys, violations


# ── Pass 2: parse handlers/*.py into resolution snapshots ────────


def _is_deepcopy_call(node: ast.expr) -> bool:
    """Return True iff *node* is ``copy.deepcopy(...)`` or ``deepcopy(...)``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "deepcopy":
        return True
    return isinstance(func, ast.Name) and func.id == "deepcopy"


def _unwrap_dict_literal(arg: ast.expr) -> ast.Dict | None:
    """Return the inner ``ast.Dict`` if *arg* is a dict (optionally deepcopy-wrapped)."""
    if isinstance(arg, ast.Dict):
        return arg
    if (
        isinstance(arg, ast.Call)
        and _is_deepcopy_call(arg)
        and len(arg.args) == 1
        and isinstance(arg.args[0], ast.Dict)
    ):
        return arg.args[0]
    return None


def _is_handlers_assignment(
    node: ast.AST,
) -> tuple[str, ast.Dict] | None:
    """Return ``(name, dict_literal)`` for ``<name>_HANDLERS = MappingProxyType({...})``.

    Recognises both annotated and bare assignments; unwraps a wrapping
    ``copy.deepcopy(...)`` call.
    """
    target: ast.expr
    value: ast.expr | None
    if isinstance(node, ast.AnnAssign):
        target = node.target
        value = node.value
    elif isinstance(node, ast.Assign):
        if len(node.targets) != 1:
            return None
        target = node.targets[0]
        value = node.value
    else:
        return None
    if (
        not isinstance(target, ast.Name)
        or not target.id.endswith("_HANDLERS")
        or value is None
    ):
        return None
    if (
        not isinstance(value, ast.Call)
        or not isinstance(value.func, ast.Name)
        or value.func.id != "MappingProxyType"
        or len(value.args) != 1
    ):
        return None
    inner = _unwrap_dict_literal(value.args[0])
    if inner is None:
        return None
    return target.id, inner


def _collect_imports(
    tree: ast.Module,
    current_module: str,
) -> dict[str, tuple[str, str]]:
    """Return ``local_name -> (absolute_module, attr)`` from top-level imports.

    Tracks both absolute (``from x.y import z``) and relative
    (``from .y import z``) imports. *current_module* is the dotted path
    of the file being parsed (e.g. ``synthorg.meta.mcp.handlers.foo``);
    relative imports are normalised against it so the resolver always
    sees an absolute module name.

    ``from . import name`` and bare ``import x`` shapes are not tracked
    (no rebound local symbol the alias resolver would follow).
    """
    imports: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        absolute_module = _resolve_import_module(node, current_module)
        if absolute_module is None:
            continue
        for alias in node.names:
            local = alias.asname or alias.name
            imports[local] = (absolute_module, alias.name)
    return imports


def _resolve_import_module(
    node: ast.ImportFrom,
    current_module: str,
) -> str | None:
    """Return the absolute dotted module for a ``from ... import`` node.

    Handles three cases:

    * Absolute (``node.level == 0``): returns ``node.module`` verbatim,
      or ``None`` when the source omits a module entirely (an
      ill-formed import that the AST otherwise tolerates).
    * Relative within the package (``node.level >= 1``): walks up
      *current_module* by ``node.level - 1`` segments and appends
      ``node.module`` if present. ``from . import x`` (level=1, no
      module) returns the package itself; the caller still skips it
      because there is no per-name module to resolve into.
    * Walking past the project root: returns ``None`` so the resolver
      treats the import as out of scope rather than crashing.
    """
    if node.level == 0:
        return node.module
    parts = current_module.split(".")
    if node.level > len(parts):
        return None
    package_parts = parts[: len(parts) - node.level]
    base = ".".join(package_parts)
    if not node.module:
        return base or None
    if not base:
        return node.module
    return f"{base}.{node.module}"


def _collect_aliases(tree: ast.Module) -> dict[str, str]:
    """Return ``local_name -> referenced_name`` for top-level ``a = b`` bindings."""
    aliases: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        aliases[node.targets[0].id] = node.value.id
    return aliases


def _collect_funcs(
    tree: ast.Module,
) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    """Return top-level ``async def`` / ``def`` nodes keyed by name."""
    funcs: dict[str, ast.AsyncFunctionDef | ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            funcs[node.name] = node
    return funcs


def _collect_handler_dicts(tree: ast.Module) -> dict[str, dict[str, ast.expr]]:
    """Return every ``*_HANDLERS`` dict in *tree*, keyed by tool name.

    Each entry maps the assignment target name (``SETTINGS_HANDLERS``)
    to the unwrapped ``{tool_key: value_expr}`` dict literal. Non-literal
    keys / ``**`` unpacking entries are silently dropped at this layer
    (the resolver flags them when it can't find an admin key).
    """
    result: dict[str, dict[str, ast.expr]] = {}
    for node in tree.body:
        match = _is_handlers_assignment(node)
        if match is None:
            continue
        name, dict_lit = match
        entries: dict[str, ast.expr] = {}
        for key_node, value_node in zip(dict_lit.keys, dict_lit.values, strict=True):
            if key_node is None:
                continue
            key = _literal_str(key_node)
            if key is None:
                continue
            entries[key] = value_node
        result[name] = entries
    return result


def _parse_module(path: Path) -> _ModuleSnapshot:
    """Parse *path* into a snapshot of the symbols the resolver needs."""
    rel = _rel(path)
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (UnicodeDecodeError, OSError, SyntaxError) as exc:
        msg = f"failed to parse {rel}: {type(exc).__name__}: {exc}"
        raise InspectionError(msg) from exc
    return _ModuleSnapshot(
        path=path,
        rel_path=rel,
        source_lines=tuple(source.splitlines()),
        funcs=_collect_funcs(tree),
        aliases=_collect_aliases(tree),
        imports=_collect_imports(tree, _module_dotted_for_path(path)),
        handler_dicts=_collect_handler_dicts(tree),
    )


def _module_dotted_for_path(path: Path) -> str:
    """Return ``synthorg.meta.mcp.handlers.<stem>`` for a handlers/*.py path."""
    return f"{_HANDLERS_PACKAGE}.{path.stem}"


def _build_module_snapshots(
    handlers_root: Path,
) -> tuple[dict[str, _ModuleSnapshot], list[_Violation]]:
    """Return ``dotted_module -> snapshot`` for every ``handlers/*.py`` file."""
    snapshots: dict[str, _ModuleSnapshot] = {}
    violations: list[_Violation] = []
    for path in _iter_python_files(handlers_root):
        try:
            snapshot = _parse_module(path)
        except InspectionError as exc:
            violations.append(_Violation(_rel(path), 0, str(exc)))
            continue
        snapshots[_module_dotted_for_path(path)] = snapshot
    return snapshots, violations


# ── Pass 3: resolve admin keys to function defs ──────────────────


@dataclass(frozen=True)
class _HandlerSite:
    """A handler function definition resolved from a ``*_HANDLERS`` map."""

    rel_path: str
    func: ast.AsyncFunctionDef | ast.FunctionDef
    source_lines: tuple[str, ...]


def _find_dict_value_for_key(
    snapshots: dict[str, _ModuleSnapshot],
    key: str,
) -> tuple[_ModuleSnapshot, ast.expr] | None:
    """Locate the ``*_HANDLERS`` value bound to *key* across all snapshots."""
    for snapshot in snapshots.values():
        for entries in snapshot.handler_dicts.values():
            value = entries.get(key)
            if value is not None:
                return snapshot, value
    return None


def _resolve_name_to_func(
    snapshots: dict[str, _ModuleSnapshot],
    snapshot: _ModuleSnapshot,
    name: str,
    seen: frozenset[tuple[str, str]] = frozenset(),
) -> _HandlerSite | None:
    """Resolve *name* in *snapshot* through aliases + cross-module imports.

    Returns ``None`` if the name cannot be resolved to a same-package
    function def (the gate cannot inspect closures or out-of-package
    callables).
    """
    cursor_module = _module_dotted_for_path(snapshot.path)
    while True:
        ident = (cursor_module, name)
        if ident in seen:
            return None
        seen = seen | {ident}
        snap = snapshots.get(cursor_module)
        if snap is None:
            return None
        func = snap.funcs.get(name)
        if func is not None:
            return _HandlerSite(
                rel_path=snap.rel_path,
                func=func,
                source_lines=snap.source_lines,
            )
        alias_target = snap.aliases.get(name)
        if alias_target is not None:
            name = alias_target
            continue
        import_target = snap.imports.get(name)
        if import_target is not None:
            cursor_module, name = import_target
            continue
        return None


def _resolve_admin_handler(
    snapshots: dict[str, _ModuleSnapshot],
    key: str,
) -> _HandlerSite | _Violation:
    """Locate and follow the ``*_HANDLERS`` entry for *key*.

    Returns a ``_HandlerSite`` if the value resolves to a same-package
    function def. Returns a ``_Violation`` if the key has no entry, or
    its value is a factory call / unresolvable cross-module ref.
    """
    found = _find_dict_value_for_key(snapshots, key)
    if found is None:
        return _Violation(
            f"src/{_HANDLERS_PACKAGE.replace('.', '/')}",
            0,
            f"admin tool {key!r} has no entry in any *_HANDLERS map.",
        )
    snapshot, value = found
    if not isinstance(value, ast.Name):
        return _Violation(
            snapshot.rel_path,
            value.lineno,
            f"admin tool {key!r} is bound to a non-Name value "
            f"({type(value).__name__}); the gate cannot statically inspect "
            "factory-built closures. Refactor to bind a named handler def, "
            f"or annotate the def line with `# {_SUPPRESSION_MARKER} -- "
            "<reason>` once the def is reachable.",
        )
    site = _resolve_name_to_func(snapshots, snapshot, value.id)
    if site is None:
        return _Violation(
            snapshot.rel_path,
            value.lineno,
            f"admin tool {key!r} resolves to {value.id!r} which the gate "
            "cannot trace to a same-package function def (broken alias "
            "chain or out-of-package import).",
        )
    return site


# ── Per-handler guardrail check ──────────────────────────────────


def _has_opt_out_marker(site: _HandlerSite) -> bool:
    """Return True iff the handler's ``def`` line carries the opt-out marker.

    The marker may sit on any line of the parenthesised ``def`` /
    ``async def`` header (ruff-format may split a long header across
    multiple physical lines).
    """
    func = site.func
    start = func.lineno - 1
    end = func.body[0].lineno - 1 if func.body else start + 1
    return any(_SUPPRESSION_RE.search(raw) for raw in site.source_lines[start:end])


def _is_pure_value_expr(node: ast.expr) -> bool:
    """Return True iff *node* contains no Call / Await / Yield."""
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom)):
            return False
    return True


def _is_trivial_prelude_stmt(stmt: ast.stmt) -> bool:
    """Return True for statements allowed to precede the guardrail call.

    Allowed shapes: bare or annotated assignment whose value is a pure
    expression (constants, attribute reads), and the docstring Expr.
    Anything that triggers code execution (Call, Await) is non-trivial.
    """
    if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        value = stmt.value
        if value is None:
            return True
        return _is_pure_value_expr(value)
    if isinstance(stmt, ast.Expr):
        return _is_pure_value_expr(stmt.value)
    return False


def _first_meaningful_stmt(body: list[ast.stmt]) -> ast.stmt | None:
    """Return the first non-trivial statement, descending one ``try:``."""
    for stmt in body:
        if _is_trivial_prelude_stmt(stmt):
            continue
        if isinstance(stmt, ast.Try):
            return _first_meaningful_stmt(stmt.body)
        return stmt
    return None


def _is_guardrail_call(call: ast.Call) -> bool:
    """Return True iff *call* is ``require_admin_guardrails(arguments, actor)``."""
    func = call.func
    if not isinstance(func, ast.Name) or func.id != _GUARDRAIL_NAME:
        return False
    if len(call.args) != 2 or call.keywords:  # noqa: PLR2004 -- arity, not magic
        return False
    first, second = call.args
    return (
        isinstance(first, ast.Name)
        and first.id == _GUARDRAIL_ARG_NAMES[0]
        and isinstance(second, ast.Name)
        and second.id == _GUARDRAIL_ARG_NAMES[1]
    )


def _extract_guardrail_call(stmt: ast.stmt) -> ast.Call | None:
    """Return the outermost ``ast.Call`` in *stmt* iff it is the guardrail."""
    for sub in ast.walk(stmt):
        if isinstance(sub, ast.Call):
            return sub if _is_guardrail_call(sub) else None
    return None


def _check_handler(site: _HandlerSite) -> _Violation | None:
    """Return a violation if *site* is missing or misorders the guardrail call."""
    if _has_opt_out_marker(site):
        return None
    func = site.func
    first = _first_meaningful_stmt(func.body)
    if first is None:
        return _Violation(
            site.rel_path,
            func.lineno,
            f"{func.name} has an empty body; admin handlers must call "
            f"{_GUARDRAIL_NAME}(arguments, actor).",
        )
    call = _extract_guardrail_call(first)
    if call is None:
        return _Violation(
            site.rel_path,
            first.lineno,
            f"{func.name} must call {_GUARDRAIL_NAME}(arguments, actor) as "
            f"the first statement of its body (or annotate the def line "
            f"with `# {_SUPPRESSION_MARKER} -- <reason>`).",
        )
    return None


# ── File iteration helpers ───────────────────────────────────────


def _iter_python_files(root: Path) -> Iterable[Path]:
    """Yield ``*.py`` files under *root*, sorted, skipping ``__pycache__``."""
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for stable violation messages."""
    try:
        return path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


# ── Driver ───────────────────────────────────────────────────────


def _run_gate(
    domains_root: Path = _DOMAINS_ROOT,
    handlers_root: Path = _HANDLERS_ROOT,
) -> list[_Violation]:
    """Execute every pass and return the aggregated violation list."""
    violations: list[_Violation] = []
    admin_keys, key_violations = _discover_admin_keys(domains_root)
    violations.extend(key_violations)
    snapshots, snapshot_violations = _build_module_snapshots(handlers_root)
    violations.extend(snapshot_violations)

    for key in sorted(admin_keys):
        outcome = _resolve_admin_handler(snapshots, key)
        if isinstance(outcome, _Violation):
            violations.append(outcome)
            continue
        violation = _check_handler(outcome)
        if violation is not None:
            violations.append(violation)
    return violations


def _report(violations: list[_Violation]) -> int:
    """Print violations and return the pre-commit exit code."""
    if not violations:
        return 0
    for v in violations:
        print(v.render())
    print(
        "\nMCP admin_tool guardrail gate: every admin_tool handler must call"
        f"\n    {_GUARDRAIL_NAME}(arguments, actor)"
        "\nas the lexically first call in its body. The guardrail enforces"
        "\nactor attribution + confirm + reason BEFORE any mutation runs."
        "\n"
        "\nFix by inserting the call as the first line inside the handler's"
        "\n``try:`` block. If the rule legitimately doesn't apply (the"
        "\nguardrail is enforced upstream of this handler), annotate the"
        f"\n``async def`` line with ``# {_SUPPRESSION_MARKER} -- <reason>``"
        "\n(non-empty justification required).",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    The gate enumerates its own targets (the full ``domains/`` +
    ``handlers/`` subtrees), so positional ``paths`` are accepted only
    so pre-commit can pass changed files without erroring; the actual
    scan always runs against the canonical roots.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Gate on missing require_admin_guardrails(arguments, actor) "
            "calls in MCP admin_tool handlers."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Ignored (pre-commit may pass changed files).",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Explicit full-scan flag (the gate always full-scans; flag "
        "kept for parity with sibling gates).",
    )
    parser.parse_args(argv)
    return _report(_run_gate())


if __name__ == "__main__":
    sys.exit(main())
