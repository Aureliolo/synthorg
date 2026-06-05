"""Git operations for semantic conflict analysis.

Encapsulates the git plumbing (merge-base, diff, show) used by the
semantic analysis pipeline. Extracted from ``git_worktree.py`` to
keep the worktree strategy module under the 800-line budget.
"""

import asyncio
import re
from collections.abc import Callable, Coroutine
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.workspace.semantic_analyzer import filter_files
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import (
    WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
    WORKSPACE_SEMANTIC_CONFLICT,
)

type GitRunner = Callable[..., Coroutine[object, object, tuple[int, str, str]]]

if TYPE_CHECKING:
    from synthorg.engine.workspace.config import SemanticAnalysisConfig
    from synthorg.engine.workspace.models import MergeConflict, Workspace
    from synthorg.engine.workspace.semantic_analyzer import SemanticAnalyzer

logger = get_logger(__name__)

_SAFE_FILE_PATH_RE = re.compile(r"^[A-Za-z0-9_./ @\-]+$")


def _validate_file_path(file_path: str) -> bool:
    """Return True if *file_path* is safe for use as a git path arg.

    Rejects empty strings, flag-like arguments, directory traversal,
    absolute paths, and characters outside a conservative allowlist.

    Returns:
        ``True`` when ``file_path`` matches the safe allowlist and
        is free of traversal segments and leading dashes; ``False``
        otherwise.
    """
    if not file_path or file_path.startswith("-"):
        return False
    if ".." in file_path.split("/"):
        return False
    if file_path.startswith("/"):
        return False
    return bool(_SAFE_FILE_PATH_RE.match(file_path))


async def get_merge_base(
    run_git: GitRunner,
    sha_a: str,
    ref_b: str,
) -> str:
    """Find the merge base (common ancestor) of two refs.

    Args:
        run_git: Bound ``_run_git`` method from the strategy.
        sha_a: First ref (typically HEAD / main tip).
        ref_b: Second ref (typically workspace branch name).

    Returns:
        Merge base SHA, or empty string on failure.
    """
    rc, stdout, stderr = await run_git(
        "merge-base",
        sha_a,
        ref_b,
        log_event=WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
    )
    if rc != 0:
        logger.warning(
            WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
            operation="merge-base",
            sha_a=sha_a,
            ref_b=ref_b,
            error=stderr,
        )
        return ""
    return stdout.strip()


async def get_changed_files(
    run_git: GitRunner,
    base_sha: str,
    merge_sha: str,
) -> tuple[str, ...]:
    """Get files changed between two commits.

    Args:
        run_git: Bound ``_run_git`` method from the strategy.
        base_sha: Commit SHA to diff from.
        merge_sha: Commit SHA to diff to.

    Returns:
        Tuple of changed file paths (safe paths only).
    """
    rc, stdout, stderr = await run_git(
        "diff",
        "--name-only",
        f"{base_sha}..{merge_sha}",
        log_event=WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
    )
    if rc != 0:
        logger.warning(
            WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
            operation="diff",
            base_sha=base_sha,
            merge_sha=merge_sha,
            error=stderr,
        )
        return ()
    if not stdout:
        return ()
    safe: list[str] = []
    for line in stdout.splitlines():
        if not line:
            continue
        if _validate_file_path(line):
            safe.append(line)
        else:
            logger.warning(
                WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
                operation="diff",
                file=line,
                error="skipping file with unsafe path characters",
            )
    return tuple(safe)


async def get_base_sources(
    run_git: GitRunner,
    base_sha: str,
    files: tuple[str, ...],
    *,
    concurrency: int | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> dict[str, str]:
    """Read file contents at a specific commit via parallel git show.

    Provide ``semaphore`` or a positive ``concurrency`` value.  The
    previous silent default (``concurrency=10``) was removed to force
    callers to thread the value from
    :class:`SemanticAnalysisConfig.git_concurrency` so the function
    default cannot drift from the config default.  When ``semaphore``
    is provided, ``concurrency`` is ignored.

    Args:
        run_git: Bound ``_run_git`` method from the strategy.
        base_sha: Commit SHA to read from.
        files: File paths to read.
        concurrency: Maximum concurrent git show calls.  Must be ``> 0``.
            Used to build a fresh semaphore when *semaphore* is ``None``.
        semaphore: Optional shared semaphore for cross-batch
            concurrency control.  When provided, *concurrency* is
            ignored.

    Returns:
        Mapping of file path to content at the given commit.
        Files that do not exist at the given commit are omitted
        (logged at warning level).

    Raises:
        ValueError: If neither *concurrency* nor *semaphore* is
            provided, or if *concurrency* is non-positive when used
            to build a semaphore (``asyncio.Semaphore(0)`` would
            deadlock every fetch task).
    """
    if semaphore is None and concurrency is None:
        msg = (
            "get_base_sources requires either concurrency= or "
            "semaphore=; pass concurrency from "
            "SemanticAnalysisConfig.git_concurrency"
        )
        raise ValueError(msg)
    if semaphore is None and concurrency is not None and concurrency <= 0:
        msg = (
            f"get_base_sources concurrency must be > 0, got {concurrency}; "
            "asyncio.Semaphore(0) would deadlock every fetch task"
        )
        raise ValueError(msg)
    sources: dict[str, str] = {}
    # ``concurrency`` is guaranteed non-None and positive when
    # ``semaphore is None`` (both guards above rejected the invalid
    # cases); the cast keeps the type checker happy without a bare
    # ``assert`` which would be stripped under -O.
    sem = (
        semaphore
        if semaphore is not None
        else asyncio.Semaphore(cast("int", concurrency))
    )

    async def _fetch(fp: str) -> None:
        async with sem:
            if not _validate_file_path(fp):
                logger.warning(
                    WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
                    operation="show",
                    file=fp,
                    error="unsafe file path",
                )
                return
            rc, stdout, stderr = await run_git(
                "show",
                f"{base_sha}:{fp}",
                log_event=WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
            )
            if rc == 0:
                sources[fp] = stdout
            elif "does not exist in" in stderr or "fatal: Path" in stderr:
                # File does not exist at this commit (new file) -- expected
                logger.debug(
                    WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
                    operation="show",
                    base_sha=base_sha,
                    file=fp,
                    error=stderr,
                )
            else:
                logger.warning(
                    WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
                    operation="show",
                    base_sha=base_sha,
                    file=fp,
                    error=stderr,
                )

    async with asyncio.TaskGroup() as tg:
        for file_path in files:
            _ = tg.create_task(_fetch(file_path))
    return sources


async def run_semantic_analysis(  # noqa: PLR0913
    *,
    run_git: GitRunner,
    config: SemanticAnalysisConfig,
    analyzer: SemanticAnalyzer | None,
    workspace: Workspace,
    pre_merge_sha: str,
    merge_sha: str,
) -> tuple[MergeConflict, ...]:
    """Run semantic analysis on a successful merge if configured.

    Orchestrates the full pipeline: finds merge base, gets changed
    files, fetches base and merged sources from git objects, and
    invokes the analyzer. All file content is read via
    ``git show {sha}:{path}`` (never from the live checkout)
    so analysis is safe to run outside the merge lock.

    Returns ``()`` when disabled, not configured, or on failure.

    Args:
        run_git: Bound ``_run_git`` method from the strategy.
        config: Semantic analysis configuration.
        analyzer: Configured ``SemanticAnalyzer``, or ``None``.
        workspace: The merged workspace.
        pre_merge_sha: Main tip before the merge.
        merge_sha: Commit SHA after the merge.

    Returns:
        Tuple of semantic ``MergeConflict`` instances.
    """
    if analyzer is None:
        return ()
    if not pre_merge_sha:
        logger.warning(
            WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
            workspace_id=workspace.workspace_id,
            reason="missing_pre_merge_sha",
            error="Cannot run semantic analysis without pre-merge SHA",
        )
        return ()
    if not config.enabled:
        return ()
    result = await _do_analysis(
        run_git=run_git,
        config=config,
        analyzer=analyzer,
        workspace=workspace,
        pre_merge_sha=pre_merge_sha,
        merge_sha=merge_sha,
    )
    if result:
        logger.warning(
            WORKSPACE_SEMANTIC_CONFLICT,
            workspace_id=workspace.workspace_id,
            count=len(result),
        )
    return result


async def _resolve_branch_point(
    run_git: GitRunner,
    workspace: Workspace,
    pre_merge_sha: str,
) -> str:
    """Resolve the branch point for semantic analysis.

    Falls back to ``pre_merge_sha`` when merge-base lookup fails.

    Returns:
        The merge-base SHA when found, or ``pre_merge_sha`` as a
        last-known-good fallback.
    """
    branch_point = await get_merge_base(
        run_git,
        pre_merge_sha,
        workspace.branch_name,
    )
    if not branch_point:
        logger.warning(
            WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
            workspace_id=workspace.workspace_id,
            operation="merge-base-fallback",
            fallback_sha=pre_merge_sha,
            error="Could not determine merge base, falling back to pre-merge SHA",
        )
        return pre_merge_sha
    return branch_point


async def _do_analysis(  # noqa: PLR0913
    *,
    run_git: GitRunner,
    config: SemanticAnalysisConfig,
    analyzer: SemanticAnalyzer,
    workspace: Workspace,
    pre_merge_sha: str,
    merge_sha: str,
) -> tuple[MergeConflict, ...]:
    """Execute semantic analysis, returning ``()`` on failure.

    Returns:
        Tuple of semantic ``MergeConflict`` instances from the
        analyzer; ``()`` when no files survived filtering or any
        non-critical exception was logged and swallowed.

    Raises:
        CancelledError: Propagated unchanged from the inner
            analyzer call so cooperative cancellation still
            unwinds the calling task group.
    """
    try:
        branch_point = await _resolve_branch_point(
            run_git,
            workspace,
            pre_merge_sha,
        )
        filtered = await _gather_filtered_files(
            run_git,
            config,
            branch_point,
            merge_sha,
        )
        if not filtered:
            return ()

        base, merged = await _fetch_sources(
            run_git,
            config,
            branch_point,
            merge_sha,
            filtered,
        )

        return await analyzer.analyze(
            workspace=workspace,
            changed_files=filtered,
            base_sources=MappingProxyType(base),
            merged_sources=MappingProxyType(merged),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
            workspace_id=workspace.workspace_id,
            context="Semantic analysis failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()


async def _gather_filtered_files(
    run_git: GitRunner,
    config: SemanticAnalysisConfig,
    branch_point: str,
    merge_sha: str,
) -> tuple[str, ...]:
    """Get changed files and filter by configured extensions.

    Returns:
        Paths changed between ``branch_point`` and ``merge_sha``
        that match the configured ``file_extensions``; ``()`` when
        no files survive filtering.
    """
    changed_files = await get_changed_files(
        run_git,
        branch_point,
        merge_sha,
    )
    if not changed_files:
        return ()
    return tuple(filter_files(changed_files, config))


async def _fetch_sources(
    run_git: GitRunner,
    config: SemanticAnalysisConfig,
    branch_point: str,
    merge_sha: str,
    filtered: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    """Fetch base and merged sources concurrently.

    Returns:
        ``(base_sources, merged_sources)`` -- each is a mapping
        from file path to the file's content at the corresponding
        commit.
    """
    sem = asyncio.Semaphore(config.git_concurrency)
    base: dict[str, str] = {}
    merged: dict[str, str] = {}

    async def _get_base() -> None:
        nonlocal base
        base = await get_base_sources(
            run_git,
            branch_point,
            filtered,
            semaphore=sem,
        )

    async def _get_merged() -> None:
        nonlocal merged
        merged = await get_base_sources(
            run_git,
            merge_sha,
            filtered,
            semaphore=sem,
        )

    async with asyncio.TaskGroup() as tg:
        _ = tg.create_task(_get_base())
        _ = tg.create_task(_get_merged())

    return base, merged
