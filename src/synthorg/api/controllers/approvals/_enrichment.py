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
from synthorg.core.run_outcome import derive_run_outcome
from synthorg.core.task import Task
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APPROVAL_ENRICH_FAILED
from synthorg.persistence.artifact_protocol import ArtifactFilterSpec
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

# Cap the artifact refs embedded per approval so a task with a large output
# set cannot bloat the queue payload; the count stays truthful.
_MAX_ARTIFACT_REFS: Final[int] = 20

TaskGetter = Callable[[str], Awaitable[Task | None]]
ProjectGetter = Callable[[str], Awaitable[Project | None]]
ArtifactLister = Callable[[str], Awaitable[Sequence[Artifact]]]


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


async def _resolve_tasks(
    task_ids: Sequence[str], *, get_task: TaskGetter
) -> dict[str, Task]:
    """Resolve each distinct task id once; drop the ones that fail or 404.

    Returns:
        Map of task id to resolved task (missing ids omitted).
    """
    resolved: dict[str, Task] = {}
    for tid in task_ids:
        try:
            task = await get_task(tid)
        except Exception as exc:  # noqa: BLE001 -- best-effort enrichment
            reraise_critical(exc)
            logger.warning(
                API_APPROVAL_ENRICH_FAILED,
                stage="task",
                task_id=tid,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            continue
        if task is not None:
            resolved[tid] = task
    return resolved


async def _resolve_projects(
    project_ids: Sequence[str], *, get_project: ProjectGetter
) -> dict[str, Project]:
    """Resolve each distinct project id once; drop the ones that fail or 404.

    Returns:
        Map of project id to resolved project (missing ids omitted).
    """
    resolved: dict[str, Project] = {}
    for pid in project_ids:
        try:
            project = await get_project(pid)
        except Exception as exc:  # noqa: BLE001 -- best-effort enrichment
            reraise_critical(exc)
            logger.warning(
                API_APPROVAL_ENRICH_FAILED,
                stage="project",
                project_id=pid,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            continue
        if project is not None:
            resolved[pid] = project
    return resolved


async def _resolve_artifacts(
    task_ids: Sequence[str], *, list_artifacts: ArtifactLister
) -> dict[str, tuple[Artifact, ...]]:
    """List produced artifacts per task (empty tuple on failure).

    Returns:
        Map of task id to its produced-artifact tuple.
    """
    resolved: dict[str, tuple[Artifact, ...]] = {}
    for tid in task_ids:
        try:
            produced = await list_artifacts(tid)
        except Exception as exc:  # noqa: BLE001 -- best-effort enrichment
            reraise_critical(exc)
            logger.warning(
                API_APPROVAL_ENRICH_FAILED,
                stage="artifacts",
                task_id=tid,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            produced = ()
        resolved[tid] = tuple(produced)
    return resolved


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

    produced = artifacts.get(item.task_id, ())
    run = ApprovalRunSummary(
        outcome=derive_run_outcome(
            status=task.status, produced_artifact_count=len(produced)
        ),
        produced_artifact_count=len(produced),
        artifacts=tuple(_artifact_ref(a) for a in produced[:_MAX_ARTIFACT_REFS]),
    )
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
        run=run,
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
    distinct task, project, and per-task artifact set once, then assembles
    one :class:`ApprovalContext` per approval.

    Returns:
        Map of approval id to its resolved :class:`ApprovalContext`.
    """
    task_ids = _unique_task_ids(items)
    tasks = await _resolve_tasks(task_ids, get_task=get_task)
    artifacts = await _resolve_artifacts(task_ids, list_artifacts=list_artifacts)
    project_ids = list(dict.fromkeys(task.project for task in tasks.values()))
    projects = await _resolve_projects(project_ids, get_project=get_project)
    return {
        str(item.id): _build_context(
            item,
            tasks=tasks,
            artifacts=artifacts,
            projects=projects,
            agent_name_by_id=agent_name_by_id,
        )
        for item in items
    }


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

    return await build_approval_contexts(
        items,
        get_task=backend.tasks.get,
        get_project=backend.projects.get,
        list_artifacts=_list_artifacts,
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
