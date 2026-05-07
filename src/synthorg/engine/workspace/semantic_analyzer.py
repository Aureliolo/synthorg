"""Semantic conflict detection for workspace merges.

Analyzes merged code for logical inconsistencies that git's textual
merge cannot detect: removed-name references, signature mismatches,
duplicate definitions, and import conflicts.
"""

import asyncio
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.engine.workspace.semantic_checks import (
    check_duplicate_definitions,
    check_import_conflicts,
    check_removed_references,
    check_signature_changes,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import (
    WORKSPACE_SEMANTIC_ANALYSIS_COMPLETE,
    WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
    WORKSPACE_SEMANTIC_ANALYSIS_START,
    WORKSPACE_SEMANTIC_CONFLICT,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.engine.workspace.config import SemanticAnalysisConfig
    from synthorg.engine.workspace.models import MergeConflict, Workspace

logger = get_logger(__name__)


@runtime_checkable
class SemanticAnalyzer(Protocol):
    """Protocol for semantic conflict analyzers.

    Implementations inspect merged files to detect logical conflicts
    that survived textual merge.
    """

    async def analyze(
        self,
        *,
        workspace: Workspace,
        changed_files: tuple[str, ...],
        base_sources: Mapping[str, str],
        merged_sources: Mapping[str, str],
    ) -> tuple[MergeConflict, ...]:
        """Analyze merged files for semantic conflicts.

        Args:
            workspace: The workspace that was just merged.
            changed_files: Paths of files modified by the merge.
            base_sources: Mapping of file path to source content
                before the merge.
            merged_sources: Mapping of file path to source content
                after the merge.

        Returns:
            Tuple of semantic MergeConflict instances.
        """
        ...


def filter_files(
    changed_files: tuple[str, ...],
    config: SemanticAnalysisConfig,
) -> list[str]:
    """Filter changed files by extension and limit to ``max_files``.

    Args:
        changed_files: Paths of all files changed by the merge.
        config: Semantic analysis configuration supplying
            ``file_extensions`` and ``max_files``.

    Returns:
        List of file paths whose extensions match, in input order,
        truncated to ``max_files``.
    """
    matched = [
        f
        for f in changed_files
        if any(f.endswith(ext) for ext in config.file_extensions)
    ]
    return matched[: config.max_files]


def _run_ast_checks(
    base_sources: Mapping[str, str],
    merged_sources: Mapping[str, str],
) -> tuple[MergeConflict, ...]:
    """Run all AST semantic checks and return combined results."""
    all_conflicts: list[MergeConflict] = []
    all_conflicts.extend(
        check_removed_references(
            base_sources=base_sources,
            merged_sources=merged_sources,
        ),
    )
    all_conflicts.extend(
        check_signature_changes(
            base_sources=base_sources,
            merged_sources=merged_sources,
        ),
    )
    all_conflicts.extend(
        check_duplicate_definitions(merged_sources=merged_sources),
    )
    all_conflicts.extend(
        check_import_conflicts(
            base_sources=base_sources,
            merged_sources=merged_sources,
        ),
    )
    return tuple(all_conflicts)


class AstSemanticAnalyzer:
    """Deterministic AST-based semantic conflict analyzer.

    Reads merged Python files and runs structural checks to detect
    removed-name references, signature mismatches, duplicate
    definitions, and import conflicts.

    Args:
        config: Semantic analysis configuration.
    """

    __slots__ = ("_config",)

    def __init__(self, *, config: SemanticAnalysisConfig) -> None:
        self._config = config

    async def analyze(
        self,
        *,
        workspace: Workspace,
        changed_files: tuple[str, ...],
        base_sources: Mapping[str, str],
        merged_sources: Mapping[str, str],
    ) -> tuple[MergeConflict, ...]:
        """Analyze merged Python files for semantic conflicts.

        Args:
            workspace: The workspace that was just merged.
            changed_files: Paths of files modified by the merge.
            base_sources: Mapping of file path to source content
                before the merge.
            merged_sources: Mapping of file path to source content
                after the merge.

        Returns:
            Tuple of semantic MergeConflict instances.
        """
        py_set = set(filter_files(changed_files, self._config))
        logger.info(
            WORKSPACE_SEMANTIC_ANALYSIS_START,
            workspace_id=workspace.workspace_id,
            file_count=len(py_set),
        )
        if not py_set:
            return self._log_complete(workspace.workspace_id, 0, 0)

        filtered_base = {k: v for k, v in base_sources.items() if k in py_set}
        relevant = {k: v for k, v in merged_sources.items() if k in py_set}
        if not relevant:
            return self._log_complete(workspace.workspace_id, 0, 0)

        result = _run_ast_checks(filtered_base, relevant)

        if result:
            logger.warning(
                WORKSPACE_SEMANTIC_CONFLICT,
                workspace_id=workspace.workspace_id,
                count=len(result),
            )
        return self._log_complete(
            workspace.workspace_id,
            len(relevant),
            len(result),
            conflicts=result,
        )

    @staticmethod
    def _log_complete(
        workspace_id: str,
        analyzed: int,
        count: int,
        *,
        conflicts: tuple[MergeConflict, ...] = (),
    ) -> tuple[MergeConflict, ...]:
        """Log completion and return conflicts."""
        logger.info(
            WORKSPACE_SEMANTIC_ANALYSIS_COMPLETE,
            workspace_id=workspace_id,
            analyzed=analyzed,
            conflicts=count,
        )
        return conflicts


def _deduplicate_conflicts(
    conflicts: list[MergeConflict],
) -> tuple[MergeConflict, ...]:
    """Remove duplicate conflicts by ``(file_path, description)``."""
    seen: set[tuple[str, str]] = set()
    unique: list[MergeConflict] = []
    for conflict in conflicts:
        key = (conflict.file_path, conflict.description)
        if key not in seen:
            seen.add(key)
            unique.append(conflict)
    return tuple(unique)


class CompositeSemanticAnalyzer:
    """Chains multiple semantic analyzers and deduplicates results.

    Runs all analyzers concurrently via ``asyncio.TaskGroup`` and
    collects their conflicts. If an analyzer raises an ``Exception``,
    it is logged and skipped. Cancellation (``CancelledError``)
    propagates via the ``TaskGroup`` as a ``BaseExceptionGroup``
    and is never suppressed.

    Args:
        analyzers: Tuple of analyzers to run concurrently.
    """

    __slots__ = ("_analyzers",)

    def __init__(
        self,
        *,
        analyzers: tuple[SemanticAnalyzer, ...],
    ) -> None:
        self._analyzers = analyzers

    async def analyze(
        self,
        *,
        workspace: Workspace,
        changed_files: tuple[str, ...],
        base_sources: Mapping[str, str],
        merged_sources: Mapping[str, str],
    ) -> tuple[MergeConflict, ...]:
        """Run all analyzers concurrently and return deduplicated results.

        Analyzers run as a structured ``TaskGroup``. Per-analyzer
        exceptions are caught and logged individually so a failing
        analyzer never prevents the others from completing.
        Results are collected in analyzer registration order for
        deterministic deduplication.

        Args:
            workspace: The workspace that was just merged.
            changed_files: Paths of files modified by the merge.
            base_sources: Mapping of file path to source content
                before the merge.
            merged_sources: Mapping of file path to source content
                after the merge.

        Returns:
            Deduplicated tuple of semantic conflicts from all analyzers.
        """
        n = len(self._analyzers)
        slots: list[tuple[MergeConflict, ...]] = [()] * n

        async def _run(idx: int, analyzer: SemanticAnalyzer) -> None:
            try:
                slots[idx] = await analyzer.analyze(
                    workspace=workspace,
                    changed_files=changed_files,
                    base_sources=base_sources,
                    merged_sources=merged_sources,
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
                    workspace_id=workspace.workspace_id,
                    analyzer=type(analyzer).__name__,
                    reason="analyzer_error",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        async with asyncio.TaskGroup() as tg:
            for i, analyzer in enumerate(self._analyzers):
                tg.create_task(_run(i, analyzer))

        all_conflicts: list[MergeConflict] = []
        for slot in slots:
            all_conflicts.extend(slot)
        return _deduplicate_conflicts(all_conflicts)
