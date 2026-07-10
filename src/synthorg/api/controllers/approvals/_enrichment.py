# module-kind: service
"""Batched, best-effort read-time enrichment for approval responses.

Resolves the human-readable context an operator needs to review an approval
without decoding UUIDs: the task title/status, project name, requesting-agent
name, and a run summary (outcome + produced artifacts). Resolution is batched
(each distinct id resolved once) and best-effort: a missing or unwired
dependency yields a partial context rather than failing the queue.

The resolvers and the :class:`RunOutcome` value object are reused by the
dashboard read model; keep this module dependency-light and importable.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Final

from synthorg.api.controllers.approvals._shared import (
    ApprovalAgentRef,
    ApprovalArtifactRef,
    ApprovalContext,
    ApprovalProjectRef,
    ApprovalResponse,
    ApprovalRunSummary,
    ApprovalTaskRef,
    _resolve_urgency_thresholds,
    _to_approval_response,
)
from synthorg.api.state import AppState
from synthorg.core.approval import ApprovalItem
from synthorg.core.artifact import Artifact
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.core.project import Project
from synthorg.core.run_outcome import TERMINAL_RUN_STATES, derive_run_outcome
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.state import EngineStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APPROVAL_ENRICH_FAILED
from synthorg.persistence.artifact_protocol import ArtifactFilterSpec
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

# Cap the artifact refs embedded per approval so a task with a large output
# set cannot bloat the queue payload; the count stays truthful.
_MAX_ARTIFACT_REFS: Final[int] = 20

# Bound the concurrent id resolutions per page so a large approval page cannot
# fan out one DB read per distinct task/project/artifact all at once and starve
# the connection pool. An internal safety cap, not an operator knob.
_MAX_CONCURRENT_RESOLVES: Final[int] = 16

type TaskGetter = Callable[[str], Awaitable[Task | None]]
type ProjectGetter = Callable[[str], Awaitable[Project | None]]
type ArtifactLister = Callable[[str], Awaitable[Sequence[Artifact]]]


def _semaphore_bounded[T](
    fetch: Callable[[str], Awaitable[T]], sem: asyncio.Semaphore
) -> Callable[[str], Awaitable[T]]:
    """Wrap *fetch* so each call holds *sem* for the duration of the read.

    Returns:
        The semaphore-guarded fetcher.
    """

    async def _run(id_: str) -> T:
        async with sem:
            return await fetch(id_)

    return _run


async def _run_all[T](coros: Sequence[Awaitable[T]]) -> list[T]:
    """Run *coros* concurrently, unwrapping a lone critical from the group.

    Each child is a ``_resolve_id`` call that already degrades its own
    best-effort failures to ``None``, so the only exception that reaches the
    group is a critical (``MemoryError`` / ``RecursionError``), which
    ``TaskGroup`` wraps in an ``ExceptionGroup``. Unwrap and re-raise the bare
    critical so it keeps propagating rather than being masked by the group.

    Returns:
        The child results in submission order.
    """
    try:
        async with asyncio.TaskGroup() as group:
            futures = [group.create_task(_awaited(coro)) for coro in coros]
    except* (MemoryError, RecursionError) as eg:
        raise eg.exceptions[0] from eg
    return [future.result() for future in futures]


async def _awaited[T](coro: Awaitable[T]) -> T:
    """Await *coro* (adapts a bare awaitable to ``create_task``).

    Returns:
        The awaited value.
    """
    return await coro


def _agent_ref(requested_by: str, agent_name_by_id: dict[str, str]) -> ApprovalAgentRef:
    """Build an agent ref, resolving the display name (falling back to the id).

    Returns:
        The resolved requesting-agent ref.
    """
    name = agent_name_by_id.get(normalize_ascii_lowercase(requested_by), requested_by)
    return ApprovalAgentRef(id=requested_by, name=name)


def _artifact_ref(artifact: Artifact) -> ApprovalArtifactRef:
    """Project an :class:`Artifact` onto the review-surface ref.

    Returns:
        The produced-artifact ref.
    """
    return ApprovalArtifactRef(
        id=artifact.id,
        path=artifact.path,
        type=artifact.type,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
    )


def _unique_task_ids(items: Sequence[ApprovalItem]) -> list[str]:
    """Distinct, non-empty task ids across the approval page (order-preserving).

    Returns:
        The distinct task ids, first-seen order preserved.
    """
    seen: dict[str, None] = {}
    for item in items:
        if item.task_id is not None:
            seen.setdefault(item.task_id, None)
    return list(seen)


async def _resolve_id[T](
    id_: str,
    fetch: Callable[[str], Awaitable[T]],
    *,
    stage: str,
) -> tuple[str, T | None]:
    """Resolve one id, degrading to ``None`` on a best-effort failure.

    Returns:
        ``(id, value)`` on success (``value`` may itself be ``None`` for a
        404), or ``(id, None)`` when the lookup raised.
    """
    try:
        return id_, await fetch(id_)
    except Exception as exc:  # noqa: BLE001 -- best-effort enrichment
        reraise_critical(exc)
        logger.warning(
            API_APPROVAL_ENRICH_FAILED,
            stage=stage,
            resource_id=id_,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return id_, None


async def _resolve_tasks(
    task_ids: Sequence[str], *, get_task: TaskGetter
) -> dict[str, Task]:
    """Resolve each distinct task id concurrently; drop failures and 404s.

    Returns:
        Map of task id to resolved task (missing ids omitted).
    """
    resolved = await _run_all(
        [_resolve_id(tid, get_task, stage="task") for tid in task_ids]
    )
    return {tid: task for tid, task in resolved if task is not None}


async def _resolve_projects(
    project_ids: Sequence[str], *, get_project: ProjectGetter
) -> dict[str, Project]:
    """Resolve each distinct project id concurrently; drop failures and 404s.

    Returns:
        Map of project id to resolved project (missing ids omitted).
    """
    resolved = await _run_all(
        [_resolve_id(pid, get_project, stage="project") for pid in project_ids]
    )
    return {pid: proj for pid, proj in resolved if proj is not None}


async def _resolve_artifacts(
    task_ids: Sequence[str], *, list_artifacts: ArtifactLister
) -> dict[str, tuple[Artifact, ...]]:
    """List produced artifacts per task concurrently; omit a task on failure.

    A failed listing is omitted from the map (not recorded as empty), so the
    caller can tell "no artifacts produced" (present, empty) apart from
    "could not determine" (absent) and never fabricates an EMPTY outcome.

    Returns:
        Map of task id to its produced-artifact tuple (failed tasks omitted).
    """
    resolved = await _run_all(
        [_resolve_id(tid, list_artifacts, stage="artifacts") for tid in task_ids]
    )
    return {tid: tuple(produced) for tid, produced in resolved if produced is not None}


def _build_run_summary(
    task: Task, produced: tuple[Artifact, ...] | None
) -> ApprovalRunSummary | None:
    """Build the run summary for a finished task, or ``None`` when it is not.

    ``produced`` is ``None`` when the artifact listing was unavailable. A
    summary is built only for a finished run (:data:`TERMINAL_RUN_STATES`);
    for a completed/in-review run whose artifacts could not be listed, the
    outcome is unknown (``None``) rather than a fabricated EMPTY. A FAILED
    run is failed regardless of the artifact count, so it is summarised even
    when the listing was unavailable.

    Returns:
        The run summary, or ``None`` when no truthful outcome can be shown.
    """
    if task.status not in TERMINAL_RUN_STATES:
        return None
    if produced is None and task.status != TaskStatus.FAILED:
        return None
    resolved = produced if produced is not None else ()
    return ApprovalRunSummary(
        outcome=derive_run_outcome(
            status=task.status, produced_artifact_count=len(resolved)
        ),
        produced_artifact_count=len(resolved),
        artifacts=tuple(_artifact_ref(a) for a in resolved[:_MAX_ARTIFACT_REFS]),
    )


def _build_context(
    item: ApprovalItem,
    *,
    tasks: dict[str, Task],
    artifacts: dict[str, tuple[Artifact, ...]],
    projects: dict[str, Project],
    agent_name_by_id: dict[str, str],
) -> ApprovalContext:
    """Assemble one approval's resolved context from the batched lookups.

    Returns:
        The resolved :class:`ApprovalContext` for this approval.
    """
    agent = _agent_ref(item.requested_by, agent_name_by_id)
    task = tasks.get(item.task_id) if item.task_id is not None else None
    if task is None or item.task_id is None:
        return ApprovalContext(agent=agent)

    resolved_project = projects.get(task.project)
    project_ref = (
        ApprovalProjectRef(id=task.project, name=resolved_project.name)
        if resolved_project is not None
        else None
    )
    return ApprovalContext(
        task=ApprovalTaskRef(id=str(task.id), title=task.title, status=task.status),
        project=project_ref,
        agent=agent,
        run=_build_run_summary(task, artifacts.get(item.task_id)),
    )


async def build_approval_contexts(
    items: Sequence[ApprovalItem],
    *,
    get_task: TaskGetter,
    get_project: ProjectGetter,
    list_artifacts: ArtifactLister,
    agent_name_by_id: dict[str, str],
) -> dict[str, ApprovalContext]:
    """Batch-resolve review context for a page of approvals, keyed by id.

    Pure over the injected resolvers (fully unit-testable): resolves each
    distinct task and per-task artifact set concurrently, then the distinct
    projects, then assembles one :class:`ApprovalContext` per approval. A
    single malformed row degrades to an agent-only context rather than
    failing the whole page.

    Returns:
        Map of approval id to its resolved :class:`ApprovalContext`.
    """
    task_ids = _unique_task_ids(items)
    try:
        async with asyncio.TaskGroup() as group:
            tasks_future = group.create_task(
                _resolve_tasks(task_ids, get_task=get_task)
            )
            artifacts_future = group.create_task(
                _resolve_artifacts(task_ids, list_artifacts=list_artifacts)
            )
    except* (MemoryError, RecursionError) as eg:
        # See _run_all: keep a lone critical unwrapped so it keeps propagating.
        raise eg.exceptions[0] from eg
    tasks = tasks_future.result()
    artifacts = artifacts_future.result()
    project_ids = list(dict.fromkeys(task.project for task in tasks.values()))
    projects = await _resolve_projects(project_ids, get_project=get_project)
    return {
        str(item.id): _context_or_agent_only(
            item,
            tasks=tasks,
            artifacts=artifacts,
            projects=projects,
            agent_name_by_id=agent_name_by_id,
        )
        for item in items
    }


def _context_or_agent_only(
    item: ApprovalItem,
    *,
    tasks: dict[str, Task],
    artifacts: dict[str, tuple[Artifact, ...]],
    projects: dict[str, Project],
    agent_name_by_id: dict[str, str],
) -> ApprovalContext:
    """Build one approval's context, degrading a bad row to agent-only.

    Returns:
        The resolved context, or an agent-only context when assembly raised.
    """
    try:
        return _build_context(
            item,
            tasks=tasks,
            artifacts=artifacts,
            projects=projects,
            agent_name_by_id=agent_name_by_id,
        )
    except Exception as exc:  # noqa: BLE001 -- one bad row must not fail the page
        reraise_critical(exc)
        # Assembly runs over already-resolved lookups, so a raise here is a
        # programming/schema defect (a shape the DTO cannot express), not the
        # expected 404 the per-id resolvers degrade at WARNING. Log at ERROR.
        logger.error(
            API_APPROVAL_ENRICH_FAILED,
            stage="assemble",
            resource_id=str(item.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ApprovalContext(agent=_agent_ref(item.requested_by, agent_name_by_id))


async def _agent_name_map(app_state: AppState) -> dict[str, str]:
    """Resolve the config agents once into an id -> display-name map.

    Returns:
        Map of normalised agent id to display name (empty on failure).
    """
    try:
        agents = await config_resolver_of(app_state).get_agents()
    except Exception as exc:  # noqa: BLE001 -- best-effort enrichment
        reraise_critical(exc)
        logger.warning(
            API_APPROVAL_ENRICH_FAILED,
            stage="agents",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return {}
    return {normalize_ascii_lowercase(str(a.id)): a.name for a in agents}


async def resolve_approval_context(
    app_state: AppState, items: Sequence[ApprovalItem]
) -> dict[str, ApprovalContext]:
    """Resolve review context for a page of approvals from application state.

    Wires the batched resolvers to the persistence backend and config
    resolver. When persistence is unwired the response still carries the
    resolved agent name (from config); every field remains best-effort.

    Returns:
        Map of approval id to its resolved :class:`ApprovalContext`.
    """
    if not items:
        return {}
    agent_name_by_id = await _agent_name_map(app_state)
    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None:
        return {
            str(item.id): ApprovalContext(
                agent=_agent_ref(item.requested_by, agent_name_by_id)
            )
            for item in items
        }

    async def _list_artifacts(task_id: str) -> Sequence[Artifact]:
        return await backend.artifacts.query(ArtifactFilterSpec(task_id=task_id))

    # Read tasks through the TaskEngine (the canonical single-writer read
    # seam) when it is wired, falling back to the repo when it is not, so
    # enrichment routes through the service layer rather than reaching past
    # it for the task read.
    task_engine = app_state.slice(EngineStateSlice).task_engine
    get_task: TaskGetter = (
        task_engine.get_task if task_engine is not None else backend.tasks.get
    )

    # One shared semaphore across all three resolver families bounds the total
    # concurrent DB reads for a page, so a large page cannot exhaust the pool.
    sem = asyncio.Semaphore(_MAX_CONCURRENT_RESOLVES)

    return await build_approval_contexts(
        items,
        get_task=_semaphore_bounded(get_task, sem),
        get_project=_semaphore_bounded(backend.projects.get, sem),
        list_artifacts=_semaphore_bounded(_list_artifacts, sem),
        agent_name_by_id=agent_name_by_id,
    )


async def build_approval_response(
    app_state: AppState, item: ApprovalItem
) -> ApprovalResponse:
    """Build a single fully-enriched approval response (urgency + context).

    The reusable single-item counterpart to the list path: resolves the
    urgency thresholds and review context, then projects onto the wire DTO.
    Used by the WebSocket publisher so a live queue upsert carries the same
    resolved names + run summary an operator sees on the page.

    Returns:
        The fully-enriched approval response.
    """
    now = datetime.now(UTC)
    critical_seconds, high_seconds = await _resolve_urgency_thresholds(app_state)
    contexts = await resolve_approval_context(app_state, (item,))
    return _to_approval_response(
        item,
        now=now,
        urgency_critical_seconds=critical_seconds,
        urgency_high_seconds=high_seconds,
        context=contexts.get(str(item.id)),
    )
