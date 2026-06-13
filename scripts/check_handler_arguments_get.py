#!/usr/bin/env python3
"""Pre-push gate: typed-args handlers never touch the raw ``arguments`` dict.

Every MCP tool registered with an ``args_model=`` kwarg has its raw
``arguments`` payload validated against that Pydantic model by the invoker
(``meta/mcp/invoker.py``) BEFORE the handler runs. The handler must therefore
read its inputs by narrowing the same dict ONCE through
:func:`synthorg.meta.mcp.handlers._mcp_handler_common.typed_args` and then
access typed fields -- never by reaching back into ``arguments`` with
``arguments.get(...)`` / ``arguments[...]`` or the legacy ``common_args``
coercion helpers. Doing both re-introduces the stringly-typed boundary the
args model exists to remove.

The gate works in three AST passes, mirroring
``scripts/check_mcp_admin_tool_guardrails.py``:

1. Walk every ``src/synthorg/meta/mcp/domains/*.py`` file. Each
   ``read_tool`` / ``write_tool`` / ``admin_tool`` / ``tool_def`` call that
   carries an ``args_model=`` keyword yields a handler key
   ``f"synthorg_{domain}_{action}"`` (domain + action must be string
   literals; a non-literal form is a hard error so a hidden registration
   cannot slip past).

2. For each handler key, walk every ``handlers/*.py`` file looking for that
   key in a ``*_HANDLERS = MappingProxyType({...})`` assignment and resolve
   the bound value to a same-package ``async def`` (following ``_alias =
   name`` rebinds and cross-module imports). A factory-built closure
   (the value is not a plain ``Name``) or an unresolvable cross-module ref
   is SKIPPED: the gate cannot inspect a closure body, and those handlers
   keep their own validation.

3. For the resolved function def, fail if its body references the
   ``arguments`` parameter anywhere other than as a direct argument to
   ``typed_args(arguments, ...)`` or ``require_admin_guardrails(arguments,
   actor)``. Any ``arguments.get(...)`` / ``arguments[...]`` / pass-through
   to a coercion helper is a violation.

Per-line opt-out (on the ``def`` line or its header)::

    # lint-allow: handler-arguments-get -- <reason>
    async def _foo(...):

The justification after ``--`` is required and must be non-empty. The marker
is the sanctioned path for handlers cataloged as args-model contract
mismatches (the model contradicts the handler's real read surface).

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

_BUILDER_NAMES: Final[frozenset[str]] = frozenset(
    {"read_tool", "write_tool", "admin_tool", "tool_def"},
)
_ARGS_MODEL_KW: Final[str] = "args_model"
_ARGUMENTS_PARAM: Final[str] = "arguments"
_ALLOWED_CALLS: Final[frozenset[str]] = frozenset(
    {"typed_args", "require_admin_guardrails"},
)
_TOOL_NAME_PREFIX: Final[str] = "synthorg_"
_HANDLERS_PACKAGE: Final[str] = "synthorg.meta.mcp.handlers"

# ── Suppression marker ───────────────────────────────────────────

_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"\blint-allow:\s*handler-arguments-get\s*--\s*\S",
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
    dotted: str
    source_lines: tuple[str, ...]
    funcs: dict[str, ast.AsyncFunctionDef | ast.FunctionDef]
    aliases: dict[str, str]
    imports: dict[str, tuple[str, str]]
    str_constants: dict[str, str]
    handler_dicts: dict[str, dict[str, ast.expr]]


# ── Pass 1: discover typed-args handler keys (domains/) ──────────


def _literal_str(node: ast.expr) -> str | None:
    """Return *node*'s value if it is a string ``ast.Constant``, else ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _has_args_model_kw(call: ast.Call) -> bool:
    """Return True iff *call* carries a non-``None`` ``args_model=`` keyword."""
    for kw in call.keywords:
        if kw.arg != _ARGS_MODEL_KW:
            continue
        # ``args_model=None`` is the no-model default; treat it as absent.
        return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
    return False


def _tool_key(call: ast.Call) -> str | None:
    """Return the handler key derived from a builder call.

    Both ``domain`` and ``action`` must be string literals (positional or
    keyword); a non-literal form is rejected by the caller.
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


def _collect_builder_symbols(tree: ast.Module) -> set[str]:
    """Return every local name that resolves to a tool-builder function."""
    symbols: set[str] = set(_BUILDER_NAMES)
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name in _BUILDER_NAMES:
                symbols.add(alias.asname or alias.name)
    return symbols


def _is_builder_call(func: ast.expr, symbols: set[str]) -> bool:
    """Return True for a builder call (bare, aliased, or attribute form)."""
    if isinstance(func, ast.Name):
        return func.id in symbols
    return isinstance(func, ast.Attribute) and func.attr in _BUILDER_NAMES


def _scan_domains_file(path: Path) -> tuple[set[str], list[_Violation]]:
    """Return ``(handler_keys, violations)`` from a single ``domains/*.py``."""
    rel = _rel(path)
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (UnicodeDecodeError, OSError, SyntaxError) as exc:
        msg = f"failed to parse {rel}: {type(exc).__name__}: {exc}"
        raise InspectionError(msg) from exc

    builder_symbols = _collect_builder_symbols(tree)
    keys: set[str] = set()
    violations: list[_Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_builder_call(node.func, builder_symbols):
            continue
        if not _has_args_model_kw(node):
            continue
        key = _tool_key(node)
        if key is None:
            violations.append(
                _Violation(
                    rel,
                    node.lineno,
                    "tool builder with args_model= requires literal domain + "
                    "action strings; the gate cannot derive a handler key from "
                    "variables.",
                ),
            )
            continue
        keys.add(key)
    return keys, violations


def _discover_typed_keys(domains_root: Path) -> tuple[set[str], list[_Violation]]:
    """Aggregate args-model handler keys across every ``domains/*.py`` file."""
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


def _is_handlers_assignment(node: ast.AST) -> tuple[str, ast.Dict] | None:
    """Return ``(name, dict_literal)`` for ``<name>_HANDLERS = MappingProxyType({...})``."""
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
    """Return ``local_name -> (absolute_module, attr)`` from top-level imports."""
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
    """Return the absolute dotted module for a ``from ... import`` node."""
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


def _collect_str_constants(tree: ast.Module) -> dict[str, str]:
    """Return ``local_name -> value`` for top-level ``NAME = "literal"`` bindings."""
    constants: dict[str, str] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if not isinstance(target, ast.Name) or value is None:
            continue
        literal = _literal_str(value)
        if literal is not None:
            constants[target.id] = literal
    return constants


def _resolve_key(node: ast.expr, str_constants: dict[str, str]) -> str | None:
    """Return a dict key's string value: a literal or a module-level constant."""
    literal = _literal_str(node)
    if literal is not None:
        return literal
    if isinstance(node, ast.Name):
        return str_constants.get(node.id)
    return None


def _collect_handler_dicts(
    tree: ast.Module,
    str_constants: dict[str, str],
) -> dict[str, dict[str, ast.expr]]:
    """Return every ``*_HANDLERS`` dict in *tree*, keyed by tool name.

    Dict keys may be string literals or module-level string constants
    (``_TOOL_RUN = "synthorg_research_run"``); both resolve to the tool name.
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
            key = _resolve_key(key_node, str_constants)
            if key is None:
                continue
            entries[key] = value_node
        result[name] = entries
    return result


def _parse_module(path: Path, handlers_root: Path) -> _ModuleSnapshot:
    """Parse *path* into a snapshot of the symbols the resolver needs."""
    rel = _rel(path)
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (UnicodeDecodeError, OSError, SyntaxError) as exc:
        msg = f"failed to parse {rel}: {type(exc).__name__}: {exc}"
        raise InspectionError(msg) from exc
    dotted = _module_dotted_for_path(path, handlers_root)
    str_constants = _collect_str_constants(tree)
    return _ModuleSnapshot(
        path=path,
        rel_path=rel,
        dotted=dotted,
        source_lines=tuple(source.splitlines()),
        funcs=_collect_funcs(tree),
        aliases=_collect_aliases(tree),
        imports=_collect_imports(tree, dotted),
        str_constants=str_constants,
        handler_dicts=_collect_handler_dicts(tree, str_constants),
    )


def _module_dotted_for_path(path: Path, handlers_root: Path) -> str:
    """Return the dotted module path for a handlers/*.py path under *handlers_root*."""
    rel = path.resolve().relative_to(handlers_root.resolve()).with_suffix("")
    suffix = ".".join(rel.parts)
    return f"{_HANDLERS_PACKAGE}.{suffix}"


def _build_module_snapshots(
    handlers_root: Path,
) -> tuple[dict[str, _ModuleSnapshot], list[_Violation]]:
    """Return ``dotted_module -> snapshot`` for every ``handlers/*.py`` file."""
    snapshots: dict[str, _ModuleSnapshot] = {}
    violations: list[_Violation] = []
    for path in _iter_python_files(handlers_root):
        try:
            snapshot = _parse_module(path, handlers_root)
        except InspectionError as exc:
            violations.append(_Violation(_rel(path), 0, str(exc)))
            continue
        snapshots[snapshot.dotted] = snapshot
    return snapshots, violations


# ── Pass 3: resolve typed-args keys to function defs ─────────────


@dataclass(frozen=True)
class _HandlerSite:
    """A handler function definition resolved from a ``*_HANDLERS`` map."""

    rel_path: str
    func: ast.AsyncFunctionDef | ast.FunctionDef
    source_lines: tuple[str, ...]


def _find_dict_value_for_key(
    snapshots: dict[str, _ModuleSnapshot],
    key: str,
) -> list[tuple[_ModuleSnapshot, ast.expr]]:
    """Return every ``*_HANDLERS`` value bound to *key* across all snapshots."""
    matches: list[tuple[_ModuleSnapshot, ast.expr]] = []
    for snapshot in snapshots.values():
        for entries in snapshot.handler_dicts.values():
            value = entries.get(key)
            if value is not None:
                matches.append((snapshot, value))
    return matches


def _resolve_name_to_func(
    snapshots: dict[str, _ModuleSnapshot],
    snapshot: _ModuleSnapshot,
    name: str,
    seen: frozenset[tuple[str, str]] = frozenset(),
) -> _HandlerSite | None:
    """Resolve *name* in *snapshot* through aliases + cross-module imports."""
    cursor_module = snapshot.dotted
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


def _resolve_handler(
    snapshots: dict[str, _ModuleSnapshot],
    key: str,
) -> _HandlerSite | _Violation | None:
    """Locate and follow the ``*_HANDLERS`` entry for *key*.

    Returns a ``_HandlerSite`` if the value resolves to a same-package
    function def, ``None`` when the handler is a factory-built closure or an
    unresolvable cross-module ref (SKIPPED -- the gate cannot inspect those),
    or a ``_Violation`` when the key has no entry at all / is ambiguously
    registered in multiple maps.
    """
    matches = _find_dict_value_for_key(snapshots, key)
    if not matches:
        return _Violation(
            f"src/{_HANDLERS_PACKAGE.replace('.', '/')}",
            0,
            f"tool {key!r} (wired with args_model) has no entry in any *_HANDLERS map.",
        )
    if len(matches) > 1:
        offending = ", ".join(sorted(match[0].rel_path for match in matches))
        return _Violation(
            f"src/{_HANDLERS_PACKAGE.replace('.', '/')}",
            0,
            f"tool {key!r} is declared in multiple *_HANDLERS maps "
            f"({offending}); resolution is ambiguous and must be deduplicated.",
        )
    snapshot, value = matches[0]
    if not isinstance(value, ast.Name):
        # Factory-built closure: not statically inspectable. Skip.
        return None
    return _resolve_name_to_func(snapshots, snapshot, value.id)


# ── Per-handler banned-reference check ───────────────────────────


def _has_opt_out_marker(site: _HandlerSite) -> bool:
    """Return True iff the handler carries the opt-out marker above/on its def.

    The marker may sit inline on the parenthesised ``def`` header (ruff-format
    can split a long header across lines) or anywhere in the contiguous block
    of comment lines immediately preceding the def -- where a multi-line
    cataloged-mismatch justification lives.
    """
    func = site.func
    lines = site.source_lines
    header_end = func.body[0].lineno - 1 if func.body else func.lineno
    start = func.lineno - 1
    if func.decorator_list:
        start = min(d.lineno for d in func.decorator_list) - 1
    if any(_SUPPRESSION_RE.search(raw) for raw in lines[start:header_end]):
        return True
    # Walk upward through the contiguous comment block above the def.
    cursor = start - 1
    while cursor >= 0 and lines[cursor].lstrip().startswith("#"):
        if _SUPPRESSION_RE.search(lines[cursor]):
            return True
        cursor -= 1
    return False


def _allowed_arguments_nodes(func: ast.AST) -> set[int]:
    """Return the id()s of ``arguments`` Names used in an allowed call.

    Allowed: ``arguments`` passed as a direct positional or keyword argument
    to ``typed_args(...)`` or ``require_admin_guardrails(...)``.
    """
    allowed: set[int] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if not (isinstance(callee, ast.Name) and callee.id in _ALLOWED_CALLS):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Name) and arg.id == _ARGUMENTS_PARAM:
                allowed.add(id(arg))
        for kw in node.keywords:
            if isinstance(kw.value, ast.Name) and kw.value.id == _ARGUMENTS_PARAM:
                allowed.add(id(kw.value))
    return allowed


def _first_banned_reference(
    func: ast.AsyncFunctionDef | ast.FunctionDef,
) -> ast.Name | None:
    """Return the first disallowed ``arguments`` reference, or ``None``."""
    allowed = _allowed_arguments_nodes(func)
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Name)
            and node.id == _ARGUMENTS_PARAM
            and id(node) not in allowed
        ):
            return node
    return None


def _check_handler(site: _HandlerSite) -> _Violation | None:
    """Return a violation if *site* touches ``arguments`` outside the allowed calls."""
    if _has_opt_out_marker(site):
        return None
    banned = _first_banned_reference(site.func)
    if banned is None:
        return None
    return _Violation(
        site.rel_path,
        banned.lineno,
        f"{site.func.name} has a wired args_model but reads the raw "
        f"'arguments' dict here; narrow it once via typed_args(arguments, "
        "XxxArgs) and access typed fields instead (or annotate the def with "
        "`# lint-allow: handler-arguments-get -- <reason>` for a cataloged "
        "args-model contract mismatch).",
    )


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
    typed_keys, key_violations = _discover_typed_keys(domains_root)
    violations.extend(key_violations)
    snapshots, snapshot_violations = _build_module_snapshots(handlers_root)
    violations.extend(snapshot_violations)

    for key in sorted(typed_keys):
        outcome = _resolve_handler(snapshots, key)
        if outcome is None:
            continue  # factory-built / unresolvable closure: skipped
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
        "\nMCP typed-args gate: every handler whose tool is registered with"
        "\nan args_model= must narrow the validated payload ONCE via"
        "\n    typed_args(arguments, XxxArgs)"
        "\nand then read typed fields. Reaching back into the raw 'arguments'"
        "\ndict (``arguments.get`` / ``arguments[...]`` / a coercion helper)"
        "\nre-introduces the stringly-typed boundary the model removes."
        "\n"
        "\nIf the args model genuinely cannot express the handler's read"
        "\nsurface (a cataloged contract mismatch), annotate the ``def`` line"
        "\nwith ``# lint-allow: handler-arguments-get -- <reason>``.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    The gate enumerates its own targets (the full ``domains/`` +
    ``handlers/`` subtrees), so positional ``paths`` are accepted only so
    pre-commit can pass changed files without erroring; the scan always runs
    against the canonical roots.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Gate on raw 'arguments' access in MCP handlers wired with an args_model."
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
        help="Explicit full-scan flag (the gate always full-scans; flag kept "
        "for parity with sibling gates).",
    )
    parser.parse_args(argv)
    return _report(_run_gate())


if __name__ == "__main__":
    sys.exit(main())
