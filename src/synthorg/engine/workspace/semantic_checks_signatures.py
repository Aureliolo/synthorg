"""Signature-change semantic conflict detection for Python files.

Compares function signatures between base and merged sources and flags
call sites whose argument shape is no longer compatible with the merged
signature. Shares the AST primitives in :mod:`semantic_checks`.
"""

import ast
from typing import TYPE_CHECKING, Final, NamedTuple

from synthorg.core.enums import ConflictType
from synthorg.engine.workspace.models import MergeConflict
from synthorg.engine.workspace.semantic_checks import _safe_parse, _top_level_names

if TYPE_CHECKING:
    from collections.abc import Mapping

_VARIADIC_ARG_SENTINEL: Final[int] = 999


def _function_min_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Return the minimum number of positional arguments required.

    Counts both positional-only and regular args, excluding those
    with defaults.
    """
    args = node.args
    total = len(args.posonlyargs) + len(args.args)
    return total - len(args.defaults)


def _function_max_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Return the maximum number of positional arguments accepted.

    Returns ``_VARIADIC_ARG_SENTINEL`` if ``*args`` is present.
    """
    if node.args.vararg is not None:
        return _VARIADIC_ARG_SENTINEL
    return len(node.args.posonlyargs) + len(node.args.args)


def _function_param_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    """Return all parameter names accepted by a function.

    Returns:
        Set of every parameter name (positional, positional-only,
        keyword-only, ``*args``, and ``**kwargs``).
    """
    names: set[str] = set()
    for arg in node.args.args:
        names.add(arg.arg)
    for arg in node.args.posonlyargs:
        names.add(arg.arg)
    for arg in node.args.kwonlyargs:
        names.add(arg.arg)
    if node.args.vararg:
        names.add(node.args.vararg.arg)
    if node.args.kwarg:
        names.add(node.args.kwarg.arg)
    return names


def _call_keyword_names(call: ast.Call) -> set[str]:
    """Return keyword argument names used in a call.

    Returns:
        Set of keyword names supplied at the call site (``**kwargs``
        unpacks are skipped).
    """
    return {kw.arg for kw in call.keywords if kw.arg is not None}


def _find_calls_to(tree: ast.Module, name: str) -> list[ast.Call]:
    """Find all direct calls to a named function in the AST.

    Returns:
        List of :class:`ast.Call` nodes whose immediate function is
        a bare ``Name`` matching ``name``.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]


class _SigInfo(NamedTuple):
    old_min: int
    old_max: int
    new_min: int
    new_max: int
    new_params: frozenset[str]
    required_kwonly: frozenset[str]


def _collect_changed_sigs(
    base_sources: Mapping[str, str],
    merged_sources: Mapping[str, str],
) -> dict[str, _SigInfo]:
    """Find functions whose signatures changed between base and merged.

    Returns:
        Mapping from function name to
        (old_min, old_max, new_min, new_max, new_param_names).
    """
    changed: dict[str, _SigInfo] = {}
    for file_path, base_src in base_sources.items():
        merged_src = merged_sources.get(file_path)
        if merged_src is None:
            continue
        base_tree = _safe_parse(base_src, file_path)
        merged_tree = _safe_parse(merged_src, file_path)
        if base_tree is None or merged_tree is None:
            continue
        _compare_signatures(
            _top_level_names(base_tree),
            _top_level_names(merged_tree),
            changed,
        )
    return changed


def _compare_signatures(
    base_names: dict[str, ast.stmt],
    merged_names: dict[str, ast.stmt],
    out: dict[str, _SigInfo],
) -> None:
    """Compare function signatures and record changes in *out*."""
    func_types = ast.FunctionDef | ast.AsyncFunctionDef
    for name, base_node in base_names.items():
        merged_node = merged_names.get(name)
        if merged_node is None:
            continue
        if not isinstance(base_node, func_types):
            continue
        if not isinstance(merged_node, func_types):
            continue

        old_min = _function_min_args(base_node)
        old_max = _function_max_args(base_node)
        new_min = _function_min_args(merged_node)
        new_max = _function_max_args(merged_node)
        new_params = _function_param_names(merged_node)
        old_params = _function_param_names(base_node)
        has_kwargs = merged_node.args.kwarg is not None

        # Track new required keyword-only params not in old signature
        new_required_kwonly = frozenset(
            arg.arg
            for arg, default in zip(
                merged_node.args.kwonlyargs,
                merged_node.args.kw_defaults,
                strict=True,
            )
            if default is None
        )
        old_required_kwonly = frozenset(
            arg.arg
            for arg, default in zip(
                base_node.args.kwonlyargs,
                base_node.args.kw_defaults,
                strict=True,
            )
            if default is None
        )
        added_required_kwonly = new_required_kwonly - old_required_kwonly

        # Gaining variadic *args makes new_max the sentinel, a non-restricting widening
        max_restricted = new_max not in (
            old_max,
            _VARIADIC_ARG_SENTINEL,
        )
        if (
            old_min != new_min
            or max_restricted
            or old_params - new_params
            or added_required_kwonly
        ):
            out[name] = _SigInfo(
                old_min=old_min,
                old_max=old_max,
                new_min=new_min,
                new_max=new_max,
                new_params=(frozenset(new_params) if not has_kwargs else frozenset()),
                required_kwonly=added_required_kwonly,
            )


def _check_call_compat(
    file_path: str,
    name: str,
    call: ast.Call,
    sig: _SigInfo,
) -> MergeConflict | None:
    """Check a single call against the new signature.

    Returns:
        A :class:`MergeConflict` when the call shape (positional
        count, removed keyword, or missing required keyword-only) is
        incompatible with the merged signature; ``None`` when the
        call is still compatible.
    """
    new_min, new_max, new_params = sig.new_min, sig.new_max, sig.new_params
    pos_count = len(call.args)
    if pos_count < new_min or pos_count > new_max:
        if new_max == _VARIADIC_ARG_SENTINEL:
            args_desc = f"at least {new_min}"
        else:
            args_desc = f"{new_min}-{new_max}"
        return MergeConflict(
            file_path=file_path,
            conflict_type=ConflictType.SEMANTIC,
            description=(
                f"Calls '{name}' with {pos_count} positional "
                f"argument(s) but merged signature "
                f"requires {args_desc}"
            ),
        )
    if new_params:
        invalid_kws = _call_keyword_names(call) - new_params
        if invalid_kws:
            return MergeConflict(
                file_path=file_path,
                conflict_type=ConflictType.SEMANTIC,
                description=(
                    f"Calls '{name}' with keyword argument(s) "
                    f"{sorted(invalid_kws)} removed from merged "
                    f"signature"
                ),
            )
    if sig.required_kwonly:
        provided_kws = _call_keyword_names(call)
        missing_kwonly = sig.required_kwonly - provided_kws
        if missing_kwonly:
            return MergeConflict(
                file_path=file_path,
                conflict_type=ConflictType.SEMANTIC,
                description=(
                    f"Calls '{name}' without required "
                    f"keyword-only argument(s) "
                    f"{sorted(missing_kwonly)} added in "
                    f"merged signature"
                ),
            )
    return None


def check_signature_changes(
    *,
    base_sources: Mapping[str, str],
    merged_sources: Mapping[str, str],
) -> tuple[MergeConflict, ...]:
    """Detect function signature changes that may break callers.

    Finds functions whose required parameter count changed between
    base and merged, then checks if callers in other merged files
    still pass the old number of arguments.

    Args:
        base_sources: Mapping of file path to source code before merge.
        merged_sources: Mapping of file path to source code after merge.

    Returns:
        Tuple of semantic conflicts for signature incompatibilities.
    """
    if not base_sources or not merged_sources:
        return ()

    changed_sigs = _collect_changed_sigs(base_sources, merged_sources)
    if not changed_sigs:
        return ()

    conflicts: list[MergeConflict] = []
    for file_path, merged_src in merged_sources.items():
        merged_tree = _safe_parse(merged_src, file_path)
        if merged_tree is None:
            continue
        for name, sig in changed_sigs.items():
            for call in _find_calls_to(merged_tree, name):
                conflict = _check_call_compat(
                    file_path,
                    name,
                    call,
                    sig,
                )
                if conflict is not None:
                    conflicts.append(conflict)
    return tuple(conflicts)
