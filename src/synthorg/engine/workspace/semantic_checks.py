"""AST-based semantic conflict checks for Python files.

Pure functions that take parsed source maps and return detected
conflicts. Each function handles one category of semantic conflict.
Files with syntax errors are silently skipped (logged at DEBUG).
"""

import ast
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from synthorg.engine.workspace.enums import ConflictType
from synthorg.engine.workspace.models import MergeConflict
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import (
    WORKSPACE_SEMANTIC_PARSE_SKIP,
)

logger = get_logger(__name__)


def _safe_parse(source: str, filename: str) -> ast.Module | None:
    """Parse source code, returning None on syntax errors.

    Returns:
        The parsed :class:`ast.Module` on success; ``None`` when
        parsing raises ``SyntaxError`` (logged at DEBUG level).
    """
    try:
        return ast.parse(source, filename=filename)
    except SyntaxError as exc:
        logger.debug(
            WORKSPACE_SEMANTIC_PARSE_SKIP,
            file=filename,
            reason="syntax_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            syntax_lineno=exc.lineno,
        )
        return None


def _top_level_names(tree: ast.Module) -> dict[str, ast.stmt]:
    """Extract top-level function, class, and assignment names.

    Only handles plain assignments (ast.Assign), not annotated
    assignments (ast.AnnAssign) to reduce false positives from
    type stubs and type aliases.

    Returns:
        Mapping from name to the AST node that defines it.
    """
    names: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(
            node,
            ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        ):
            names[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names[target.id] = node
    return names


def _all_name_references(tree: ast.Module) -> set[str]:
    """Collect names referenced in Load context in the module.

    Returns:
        Set of identifier strings appearing in load context across
        the entire module tree.
    """
    refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            refs.add(node.id)
    return refs


def _imported_names(tree: ast.Module) -> list[tuple[str, str, str]]:
    """Extract from-import names: (module, imported_name, alias).

    Only handles ``from X import Y`` style imports.
    Star imports are excluded.

    Returns:
        List of ``(module, imported_name, alias)`` triples for every
        ``from X import Y [as Z]`` in the module.
    """
    result: list[tuple[str, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                result.append(
                    (node.module, alias.name, alias.asname or alias.name),
                )
    return result


# ---------------------------------------------------------------------------
# Public check functions
# ---------------------------------------------------------------------------


def _collect_removed_names(
    base_sources: Mapping[str, str],
    merged_sources: Mapping[str, str],
) -> dict[str, str]:
    """Find top-level names removed between base and merged.

    Returns:
        Mapping from removed name to source file path.
    """
    removed: dict[str, str] = {}
    for file_path, base_src in base_sources.items():
        merged_src = merged_sources.get(file_path)
        if merged_src is None:
            # File was deleted -- all names are removed
            base_tree = _safe_parse(base_src, file_path)
            if base_tree is not None:
                for name in _top_level_names(base_tree):
                    removed[name] = file_path
            continue
        base_tree = _safe_parse(base_src, file_path)
        merged_tree = _safe_parse(merged_src, file_path)
        if base_tree is None or merged_tree is None:
            continue
        base_names = set(_top_level_names(base_tree))
        merged_names = set(_top_level_names(merged_tree))
        for name in base_names - merged_names:
            removed[name] = file_path
    return removed


def check_removed_references(
    *,
    base_sources: Mapping[str, str],
    merged_sources: Mapping[str, str],
) -> tuple[MergeConflict, ...]:
    """Detect references to names removed by the merge.

    Compares top-level definitions in base vs merged sources to find
    names that were removed, then checks if those names are still
    referenced in any merged file.

    Args:
        base_sources: Mapping of file path to source code before merge.
        merged_sources: Mapping of file path to source code after merge.

    Returns:
        Tuple of semantic conflicts for removed-name references.
    """
    if not base_sources or not merged_sources:
        return ()

    removed_names = _collect_removed_names(base_sources, merged_sources)
    if not removed_names:
        return ()

    conflicts: list[MergeConflict] = []
    for file_path, merged_src in merged_sources.items():
        merged_tree = _safe_parse(merged_src, file_path)
        if merged_tree is None:
            continue
        refs = _all_name_references(merged_tree)
        local_defs = set(_top_level_names(merged_tree))
        for name, source_file in removed_names.items():
            if name in refs and name not in local_defs:
                conflicts.append(
                    MergeConflict(
                        file_path=file_path,
                        conflict_type=ConflictType.SEMANTIC,
                        description=(
                            f"References '{name}' which was removed "
                            f"from '{source_file}' during merge"
                        ),
                    ),
                )
    return tuple(conflicts)


def check_duplicate_definitions(
    *,
    merged_sources: Mapping[str, str],
) -> tuple[MergeConflict, ...]:
    """Detect duplicate top-level function or class definitions.

    Two branches may independently define the same name at module
    level. After merge, the later definition silently shadows the
    earlier one.

    Args:
        merged_sources: Mapping of file path to source code after merge.

    Returns:
        Tuple of semantic conflicts for duplicate definitions.
    """
    if not merged_sources:
        return ()

    conflicts: list[MergeConflict] = []
    for file_path, source in merged_sources.items():
        tree = _safe_parse(source, file_path)
        if tree is None:
            continue

        name_counts: Counter[str] = Counter()
        for node in tree.body:
            if isinstance(
                node,
                ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
            ):
                name_counts[node.name] += 1

        for name, count in name_counts.items():
            if count > 1:
                conflicts.append(
                    MergeConflict(
                        file_path=file_path,
                        conflict_type=ConflictType.SEMANTIC,
                        description=(
                            f"Duplicate top-level definition '{name}' "
                            f"appears {count} times"
                        ),
                    ),
                )
    return tuple(conflicts)


def _file_path_to_module_stem(file_path: str) -> str:
    """Convert a Python file path to a dotted module stem.

    Strips ``.py`` suffix, converts ``__init__.py`` to the parent
    package name, removes common source root prefixes (``src/``,
    ``lib/``), and replaces path separators with dots.

    Returns:
        The dotted module path string (e.g. ``"pkg.sub.module"``)
        for the given file path.
    """
    stem = file_path.removesuffix(".py")
    if stem.endswith(("/__init__", "\\__init__")):
        sep = "/" if "/" in stem else "\\"
        stem = stem.rsplit(sep, 1)[0]
    for prefix in ("src/", "lib/"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    return stem.replace("/", ".").replace("\\", ".")


def _collect_removed_exports(
    base_sources: Mapping[str, str],
    merged_sources: Mapping[str, str],
) -> dict[str, set[str]]:
    """Find top-level definitions removed between base and merged, keyed by module stem.

    Returns:
        Mapping from dotted module stem to set of removed definition names.
    """
    removed: dict[str, set[str]] = {}
    for file_path, base_src in base_sources.items():
        merged_src = merged_sources.get(file_path)
        if merged_src is None:
            # File was deleted -- all exports are removed
            base_tree = _safe_parse(base_src, file_path)
            if base_tree is not None:
                gone = set(_top_level_names(base_tree))
                if gone:
                    removed[_file_path_to_module_stem(file_path)] = gone
            continue
        base_tree = _safe_parse(base_src, file_path)
        merged_tree = _safe_parse(merged_src, file_path)
        if base_tree is None or merged_tree is None:
            continue
        gone = set(_top_level_names(base_tree)) - set(
            _top_level_names(merged_tree),
        )
        if gone:
            removed[_file_path_to_module_stem(file_path)] = gone
    return removed


def check_import_conflicts(
    *,
    base_sources: Mapping[str, str],
    merged_sources: Mapping[str, str],
) -> tuple[MergeConflict, ...]:
    """Detect imports of names that were removed from their source module.

    When one branch removes a name from a module and another branch
    adds an import of that name, the import will fail at runtime.

    Args:
        base_sources: Mapping of file path to source code before merge.
        merged_sources: Mapping of file path to source code after merge.

    Returns:
        Tuple of semantic conflicts for broken imports.
    """
    if not base_sources or not merged_sources:
        return ()

    removed_exports = _collect_removed_exports(base_sources, merged_sources)
    if not removed_exports:
        return ()

    conflicts: list[MergeConflict] = []
    for file_path, merged_src in merged_sources.items():
        merged_tree = _safe_parse(merged_src, file_path)
        if merged_tree is None:
            continue
        for module, name, _ in _imported_names(merged_tree):
            if name in removed_exports.get(module, set()):
                conflicts.append(
                    MergeConflict(
                        file_path=file_path,
                        conflict_type=ConflictType.SEMANTIC,
                        description=(
                            f"Imports '{name}' from '{module}' but "
                            f"'{name}' was removed during merge"
                        ),
                    ),
                )
    return tuple(conflicts)
