#!/usr/bin/env python3
"""Pre-push / CI gate: no MCP handler consumes a never-wired service.

The anti-ghost-wiring gate (``check_no_ghost_wiring.py``) can only check
symbols already named in its manifest, so it cannot *discover* a brand-new
service that ships wired-in-name-only: a typed ``AppState`` slice field
(``X | None = None``) that an MCP handler reads but that nothing in the boot
path ever constructs. Such a tool is registered and callable yet returns
``not_supported`` / 503 in every deployment -- the exact "declared but
unshipped surface" defect this gate exists to prevent.

This gate closes that blind spot by *discovering* every service an MCP
handler depends on, from two AST shapes, and asserting each backing class is
either constructed in ``src/`` (a real boot-path call site, reused from
``check_no_ghost_wiring._scan_sites``) or tracked in the shared ghost-wiring
manifest:

1. **capability-gap guards** in ``meta/mcp/handlers/*.py``: a ``return
   capability_gap(...)`` inside an ``if`` whose test bottoms out at
   ``app_state.slice(SliceClass).field`` -- directly, through a module-local
   ``_x_wired(app_state)`` predicate, or through a ``service =
   _x_service(app_state)`` local. All three are reduced to their
   ``(SliceClass, field)`` subject.
2. **``*_of`` accessors** anywhere in the runtime tree: ``return
   require_service(app_state.slice(SliceClass).field, ...)`` -- the universal
   503-on-``None`` resolution point, covering services consumed without an
   explicit capability-gap guard.

``(SliceClass, field)`` resolves to the backing class through the slice's
``field: Backing | None`` annotation (indexed from every ``*state*.py``).

Fail-closed by construction: an unparseable scanned file, a capability-gap
call that reduces to no slice subject (an unrecognised guard shape), or a
subject whose slice field cannot be resolved all fail the gate rather than
silently reopening the blind spot.

Usage::

    uv run python scripts/check_mcp_capability_gap_documented.py
    uv run python scripts/check_mcp_capability_gap_documented.py --repo-root .
"""

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_no_ghost_wiring import (  # type: ignore[import-not-found]
        MANIFEST,
        RUNTIME_PREFIXES,
        _iter_runtime_py,
        _parse_manifest,
        _scan_sites,
        _Sites,
    )
else:
    from scripts.check_no_ghost_wiring import (
        MANIFEST,
        RUNTIME_PREFIXES,
        _iter_runtime_py,
        _parse_manifest,
        _scan_sites,
        _Sites,
    )

if TYPE_CHECKING:
    from collections.abc import Iterable

_HANDLERS_DIR: Final[str] = "src/synthorg/meta/mcp/handlers"

# capability_gap lives in common.py; that module + the shared handler
# helpers are the unknown-tool / routing turf (owned by the capability-gap
# routing rework), never a per-service slice guard, so scanning them would
# fail-close on non-guard capability_gap calls. Skip them.
_HANDLER_SKIP: Final[frozenset[str]] = frozenset(
    {
        "common.py",
        "_mcp_handler_common.py",
        "common_args.py",
        "common_logging.py",
    }
)

_CAPABILITY_GAP: Final[str] = "capability_gap"
_REQUIRE_SERVICE: Final[str] = "require_service"
_SLICE_METHOD: Final[str] = "slice"
_SLICE_SUFFIX: Final[str] = "StateSlice"
_ACCESSOR_SUFFIX: Final[str] = "_of"

_SliceRef = tuple[str, str]

# Handlers are ``async def``; accessors + guard helpers are ``def``. Both
# forms must be walked, so every function-shaped scan matches the pair.
_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class _Discovery:
    """One ``(SliceClass, field)`` an MCP handler depends on + where."""

    slice_ref: _SliceRef
    site: str


def _extract_slice_ref(node: ast.AST) -> _SliceRef | None:
    """Return ``(SliceClass, field)`` for an ``app_state.slice(X).field`` node.

    Returns:
        The ``(slice-class-name, field-name)`` pair, or ``None`` when *node*
        is not a slice-field attribute access.
    """
    if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Call):
        return None
    call = node.value
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == _SLICE_METHOD
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Name)
    ):
        return (call.args[0].id, node.attr)
    return None


def _slice_refs_in(node: ast.AST) -> set[_SliceRef]:
    """Return every ``app_state.slice(X).field`` subject reachable in *node*."""
    refs: set[_SliceRef] = set()
    for child in ast.walk(node):
        ref = _extract_slice_ref(child)
        if ref is not None:
            refs.add(ref)
    return refs


def _callee_name(call: ast.Call) -> str | None:
    """Return the bare or attribute name a ``Call`` invokes, else ``None``."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _module_functions(tree: ast.Module) -> dict[str, _FuncDef]:
    """Index module-level ``def`` / ``async def`` nodes by name."""
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _resolve_guard_subjects(
    func: _FuncDef,
    guard: ast.If,
    module_funcs: dict[str, _FuncDef],
) -> set[_SliceRef]:
    """Reduce a capability-gap guard to its ``(SliceClass, field)`` subjects.

    Handles the three shipped guard shapes by collecting slice subjects from
    the ``if`` test itself, from any module-local helper the test calls
    (``_x_wired(app_state)``), and from the assigned value of any local name
    the test reads (``service = _x_service(app_state)``), following a helper
    call one level into its body.

    Returns:
        The set of slice subjects the guard protects (empty when the guard
        matches no recognised shape).
    """
    subjects = _slice_refs_in(guard.test)
    for node in ast.walk(guard.test):
        if isinstance(node, ast.Call):
            name = _callee_name(node)
            if name in module_funcs:
                subjects |= _slice_refs_in(module_funcs[name])
        elif isinstance(node, ast.Name):
            subjects |= _subjects_via_local(func, node.id, module_funcs)
    return subjects


def _subjects_via_local(
    func: _FuncDef,
    local: str,
    module_funcs: dict[str, _FuncDef],
) -> set[_SliceRef]:
    """Return slice subjects from a local variable's assignment in *func*.

    The assigned value may be a slice access directly or a call into a
    module-local helper whose body reads the slice; both are followed.
    """
    subjects: set[_SliceRef] = set()
    for stmt in ast.walk(func):
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == local
            for target in stmt.targets
        ):
            continue
        subjects |= _slice_refs_in(stmt.value)
        for node in ast.walk(stmt.value):
            if isinstance(node, ast.Call):
                name = _callee_name(node)
                if name in module_funcs:
                    subjects |= _slice_refs_in(module_funcs[name])
    return subjects


def _is_capability_gap_return(node: ast.AST) -> bool:
    """Return whether *node* is ``return capability_gap(...)``."""
    return (
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Call)
        and _callee_name(node.value) == _CAPABILITY_GAP
    )


def _discover_from_handler(
    tree: ast.Module,
    rel: str,
) -> tuple[list[_Discovery], list[str]]:
    """Discover slice subjects from one handler module's capability gaps.

    Returns:
        A ``(discoveries, failures)`` pair. *failures* holds fail-closed
        messages for capability-gap calls not covered by a resolvable guard.
    """
    module_funcs = _module_functions(tree)
    discoveries: list[_Discovery] = []
    failures: list[str] = []
    covered: set[int] = set()
    for func in module_funcs.values():
        for node in ast.walk(func):
            if not isinstance(node, ast.If):
                continue
            gap_returns = [s for s in node.body if _is_capability_gap_return(s)]
            if not gap_returns:
                continue
            subjects = _resolve_guard_subjects(func, node, module_funcs)
            for stmt in gap_returns:
                covered.add(id(stmt))
            if not subjects:
                failures.append(
                    f"{rel}:{node.lineno}: capability_gap guard did not reduce "
                    "to an app_state.slice(X).field subject (unrecognised guard "
                    "shape; the discovery gate cannot map it to a backing "
                    "service)"
                )
                continue
            discoveries.extend(
                _Discovery(slice_ref=ref, site=f"{rel}:{node.lineno}")
                for ref in sorted(subjects)
            )
    failures.extend(_uncovered_gap_failures(module_funcs.values(), rel, covered))
    return discoveries, failures


def _uncovered_gap_failures(
    funcs: Iterable[_FuncDef],
    rel: str,
    covered: set[int],
) -> list[str]:
    """Return fail-closed messages for capability-gap returns with no guard."""
    return [
        f"{rel}:{stmt.lineno}: capability_gap call is not the direct body of a "
        "recognised `if <slice-field> is None` guard; the discovery gate cannot "
        "map it to a backing service"
        for func in funcs
        for stmt in ast.walk(func)
        if isinstance(stmt, ast.Return)
        and _is_capability_gap_return(stmt)
        and id(stmt) not in covered
    ]


def _accessor_calls(tree: ast.Module) -> set[str]:
    """Return the names of every ``*_of(...)`` accessor a module calls.

    Scopes the accessor-shape discovery to services an MCP handler actually
    resolves: an accessor defined in the runtime tree but never called from a
    handler is not MCP surface and is out of this gate's remit.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _callee_name(node)
            if name is not None and name.endswith(_ACCESSOR_SUFFIX):
                names.add(name)
    return names


def _discover_from_accessor(
    func: _FuncDef,
    rel: str,
) -> _Discovery | None:
    """Discover a slice subject from a ``*_of`` require_service accessor.

    Returns:
        The discovered subject, or ``None`` when *func* is not a
        ``require_service(app_state.slice(X).field, ...)`` accessor.
    """
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and _callee_name(node) == _REQUIRE_SERVICE
            and node.args
        ):
            ref = _extract_slice_ref(node.args[0])
            if ref is not None:
                return _Discovery(slice_ref=ref, site=f"{rel}:{func.lineno}")
    return None


def _annotation_class_name(annotation: ast.expr) -> str | None:
    """Return the backing class name of a ``Backing | None`` annotation.

    Returns:
        The primary class name, or ``None`` when the annotation carries no
        resolvable class (e.g. a bare ``None``).
    """
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _annotation_class_name(annotation.left) or _annotation_class_name(
            annotation.right
        )
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Subscript):
        return _annotation_class_name(annotation.value)
    return None


def _build_slice_map(repo_root: Path) -> dict[_SliceRef, str]:
    """Index ``(SliceClass, field) -> backing class`` from every ``*state*.py``.

    Slices live per feature (``organization/state.py``,
    ``api/api_core_state.py``, ...), some outside the runtime-prefix set the
    boot-path scan uses, so the map is built from the wider ``*state*.py``
    glob to avoid a false fail-closed on a legitimately-placed slice.

    Raises:
        OSError: If a matched file cannot be read.
        SyntaxError: If a matched file cannot be parsed (fail-closed).
    """
    slice_map: dict[_SliceRef, str] = {}
    src_root = repo_root / "src" / "synthorg"
    for py in sorted(src_root.rglob("*.py")):
        if "state" not in py.name.lower():
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, UnicodeDecodeError) as exc:
            msg = f"{py}: {exc}"
            raise OSError(msg) from exc
        except SyntaxError as exc:
            msg = f"{py}: {exc}"
            raise SyntaxError(msg) from exc
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not node.name.endswith(
                _SLICE_SUFFIX
            ):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(
                    stmt.target, ast.Name
                ):
                    backing = _annotation_class_name(stmt.annotation)
                    if backing is not None:
                        slice_map[node.name, stmt.target.id] = backing
    return slice_map


def _iter_handler_py(repo_root: Path) -> Iterable[tuple[Path, str]]:
    """Yield ``(path, rel)`` for each scannable MCP handler module."""
    base = repo_root / _HANDLERS_DIR
    if not base.is_dir():
        return
    for py in sorted(base.rglob("*.py")):
        if py.name in _HANDLER_SKIP:
            continue
        yield py, py.relative_to(repo_root).as_posix()


def _parse(py: Path) -> ast.Module:
    """Parse *py* into a module AST, raising with file context (fail-closed).

    Raises:
        OSError: If *py* cannot be read or decoded.
        SyntaxError: If *py* cannot be parsed.
    """
    try:
        return ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{py}: {exc}"
        raise OSError(msg) from exc
    except SyntaxError as exc:
        msg = f"{py}: {exc}"
        raise SyntaxError(msg) from exc


def _discover(repo_root: Path) -> tuple[list[_Discovery], list[str]]:
    """Discover every MCP-handler service dependency across both shapes.

    Returns:
        A ``(discoveries, failures)`` pair; *failures* are fail-closed
        messages surfaced before the wiring check runs.
    """
    discoveries: list[_Discovery] = []
    failures: list[str] = []
    accessor_names: set[str] = set()
    for py, rel in _iter_handler_py(repo_root):
        tree = _parse(py)
        handler_discoveries, handler_failures = _discover_from_handler(tree, rel)
        discoveries.extend(handler_discoveries)
        failures.extend(handler_failures)
        accessor_names |= _accessor_calls(tree)
    for py in _iter_runtime_py(repo_root):
        rel = py.relative_to(repo_root).as_posix()
        tree = _parse(py)
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in accessor_names
            ):
                found = _discover_from_accessor(node, rel)
                if found is not None:
                    discoveries.append(found)
    return discoveries, failures


def _resolve_classes(
    discoveries: list[_Discovery],
    slice_map: dict[_SliceRef, str],
) -> tuple[dict[str, list[_Discovery]], list[str]]:
    """Group discoveries by backing class; fail-closed on unresolved subjects.

    Returns:
        A ``(class -> discoveries, failures)`` pair. *failures* name any
        subject whose slice field has no resolvable backing-class annotation.
    """
    by_class: dict[str, list[_Discovery]] = {}
    failures: list[str] = []
    for discovery in discoveries:
        backing = slice_map.get(discovery.slice_ref)
        if backing is None:
            slice_cls, field = discovery.slice_ref
            failures.append(
                f"{discovery.site}: cannot resolve backing class for "
                f"{slice_cls}.{field} (no `{field}: <Class> | None` annotation "
                "found on any *state*.py slice)"
            )
            continue
        by_class.setdefault(backing, []).append(discovery)
    return by_class, failures


def _run(repo_root: Path) -> int:
    repo_root = repo_root.resolve()
    manifest_path = repo_root / MANIFEST
    if not manifest_path.is_file():
        print(f"capability-gap gate: manifest missing at {MANIFEST}")
        return 1
    try:
        manifest_symbols = {e.symbol for e in _parse_manifest(manifest_path)}
        slice_map = _build_slice_map(repo_root)
        discoveries, discover_failures = _discover(repo_root)
    except (OSError, SyntaxError, ValueError) as exc:
        print(f"capability-gap gate: scan failed (fail-closed): {exc}")
        return 1

    by_class, resolve_failures = _resolve_classes(discoveries, slice_map)
    fail_closed = discover_failures + resolve_failures
    if fail_closed:
        print("capability-gap gate: fail-closed (undiscoverable surface):")
        for line in fail_closed:
            print(f"  {line}")
        return 1

    try:
        sites = _scan_sites(repo_root, set(by_class))
    except (OSError, SyntaxError) as exc:
        print(f"capability-gap gate: cannot scan src/ (fail-closed): {exc}")
        return 1

    unwired = _unwired_classes(by_class, sites, manifest_symbols)
    if not unwired:
        return 0

    print(
        "MCP capability-gap regression -- handler-consumed services with no\n"
        "construction site in the shipped src/ boot path and no ghost-wiring\n"
        "manifest entry (registered tools that 503 in every deployment):"
    )
    for backing in sorted(unwired):
        first = by_class[backing][0]
        slice_cls, field = first.slice_ref
        print(f"  {backing} <- {slice_cls}.{field} ({first.site})")
    print(
        f"\nFix: wire the service into the boot path (a construction site under "
        f"one of {', '.join(RUNTIME_PREFIXES[:3])} ...), or, if it is a tracked "
        f"deferral, add a manifest line in {MANIFEST}. A handler-consumed "
        "service that ships unconstructed is the exact defect this gate prevents."
    )
    return 1


def _unwired_classes(
    by_class: dict[str, list[_Discovery]],
    sites: _Sites,
    manifest_symbols: set[str],
) -> set[str]:
    """Return backing classes neither constructed in src/ nor manifested."""
    unwired: set[str] = set()
    for backing in by_class:
        external = sites.calls.get(backing, set()) - sites.defs.get(backing, set())
        if not external and backing not in manifest_symbols:
            unwired.add(backing)
    return unwired


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    return _run(args.repo_root.resolve())


if __name__ == "__main__":
    sys.exit(main())
