# module-kind: code
"""Turning the references a row carries into names an operator reads.

A row stores an id because an id is what stays correct while the organisation
changes under it: staffing repoints a project's lead, reassignment repoints a
task's assignee, a task is retitled, and a name written into the row would go
stale exactly when it mattered. So the name is resolved here, at the read
boundary, once per response rather than once per row, and travels beside the
id it stands for.

The dashboard never resolves it. A browser-side lookup has to fetch the roster
first, which means an id renders on the first paint of every cold load and
renders forever for anyone the fetched page did not cover; that is not a
timing bug to tighten but the wrong place for the question.

Every resolver here is best-effort in the same direction: an unreadable source
names nobody, so the surface prints its own words for an unnamed reference and
never the key it stands for.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Final

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.display_name import display_name_or_none
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APPROVAL_ENRICH_FAILED
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

#: Bound the concurrent task reads one page may issue, so a wide page cannot
#: fan out one query per row at once and starve the connection pool.
_MAX_CONCURRENT_TITLE_READS: Final[int] = 16


async def agent_name_map(app_state: AppState) -> dict[str, str]:
    """Resolve the configured agents once into an id to display-name map.

    Best-effort: a roster that cannot be read yields an empty map, so the
    surface falls back to naming nobody rather than failing the request.

    Returns:
        Map of normalised agent id to display name (empty on failure).
    """
    try:
        agents = await config_resolver_of(app_state).get_agents()
    except Exception as exc:  # noqa: BLE001 -- best-effort enrichment
        # lint-allow: swallow-ok -- a name is context, not the response; the
        # gap is reported and every caller degrades to an unnamed actor.
        reraise_critical(exc)
        logger.warning(
            API_APPROVAL_ENRICH_FAILED,
            stage="agents",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return {}
    return {normalize_ascii_lowercase(str(a.id)): a.name for a in agents}


def resolved_actor_name(actor: str | None, names: Mapping[str, str]) -> str | None:
    """Return the name *actor* is known by, or ``None`` when it has none.

    Three outcomes, and the third is the point: the roster name when the actor
    is on it; the reference itself when that is already a word a person reads
    (a system actor, a peer label, a username); and ``None`` when it is a key.
    A key is never returned, because a surface handed one prints it.

    Args:
        actor: The stored reference, or ``None``.
        names: The map from :func:`agent_name_map`.

    Returns:
        The display name, or ``None``.
    """
    if actor is None:
        return None
    resolved = names.get(normalize_ascii_lowercase(actor))
    return resolved if resolved is not None else display_name_or_none(actor)


async def task_titles(app_state: AppState, task_ids: Iterable[str]) -> dict[str, str]:
    """Resolve each distinct task id in *task_ids* to the task's title.

    Returns:
        Map of task id to title (unresolvable ids omitted).
    """
    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None:
        return {}

    async def _title(task_id: str) -> str | None:
        task = await backend.tasks.get(task_id)
        return None if task is None else str(task.title)

    return await _named_by_id(task_ids, _title, stage="task_title")


async def project_names(
    app_state: AppState, project_ids: Iterable[str]
) -> dict[str, str]:
    """Resolve each distinct project id in *project_ids* to the project's name.

    Returns:
        Map of project id to name (unresolvable ids omitted).
    """
    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None:
        return {}

    async def _name(project_id: str) -> str | None:
        project = await backend.projects.get(project_id)
        return None if project is None else str(project.name)

    return await _named_by_id(project_ids, _name, stage="project_name")


async def _named_by_id(
    ids: Iterable[str],
    read: Callable[[str], Awaitable[str | None]],
    *,
    stage: str,
) -> dict[str, str]:
    """Read a name per distinct id, best-effort and bounded in concurrency.

    A row that cannot be read, or no longer exists, is simply absent from the
    map, so the surface says so in its own words rather than printing the key.
    Bounded because one wide page must not fan out a query per row at once and
    starve the connection pool.

    Args:
        ids: The references to resolve, deduplicated here.
        read: Reads one id, answering ``None`` when nothing is stored for it.
        stage: What is being read, for the degradation log line.

    Returns:
        Map of id to name (unresolvable ids omitted).
    """
    distinct = list(dict.fromkeys(ids))
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TITLE_READS)

    async def _one(entity_id: str) -> tuple[str, str | None]:
        try:
            async with semaphore:
                return entity_id, await read(entity_id)
        except Exception as exc:  # noqa: BLE001 -- best-effort enrichment
            # lint-allow: swallow-ok -- a name is context, not the response;
            # the gap is reported and the surface names the row as unknown.
            reraise_critical(exc)
            logger.warning(
                API_APPROVAL_ENRICH_FAILED,
                stage=stage,
                resource_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return entity_id, None

    try:
        async with asyncio.TaskGroup() as group:
            futures = [group.create_task(_one(entity_id)) for entity_id in distinct]
    except* (MemoryError, RecursionError) as eg:
        raise eg.exceptions[0] from eg
    resolved = [future.result() for future in futures]
    return {entity_id: name for entity_id, name in resolved if name is not None}


def named_actors(
    actors: Iterable[str | None], names: Mapping[str, str]
) -> dict[str, str]:
    """Resolve several references at once, keeping only the ones with names.

    Returns:
        Map of the original reference to its display name.
    """
    resolved: dict[str, str] = {}
    for actor in actors:
        if actor is None:
            continue
        name = resolved_actor_name(actor, names)
        if name is not None:
            resolved[actor] = name
    return resolved


__all__ = [
    "agent_name_map",
    "named_actors",
    "project_names",
    "resolved_actor_name",
    "task_titles",
]
