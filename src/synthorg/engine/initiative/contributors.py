# module-kind: code
"""Who actually worked an initiative.

Derived from the tasks that ran on the project, never stored on it. A
collection embedded in the project row would have to be written by every
actor that assigns a child, in the same transaction, forever; the field that
tried it was empty in every deployment, so "who contributed" read as nobody
and every non-lead retrospective learning was discarded.

The tasks already carry the answer: ``Task.assigned_to`` is written by the
same actor that made the assignment, once, on the row it already owns. This
is the rule coordination applies for ``team_size`` (participants read off
outcomes) and the rule the project graph applies to every other child
collection.

An assignee alone is not a contributor, though: ``assigned_to`` is written
when the task enters ASSIGNED, before anything runs, so a queue of work
nobody has started would otherwise read as a roomful of contributors.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.run_outcome import NEVER_RAN_STATES
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository

logger = get_logger(__name__)

#: Page size for the contributor scan. The walk is bounded by the project's
#: own task count, exactly as the progress projection's plan-task walk is.
_TASK_PAGE_SIZE: Final[int] = 200


async def initiative_contributors(
    task_repo: TaskRepository,
    *,
    project_id: NotBlankStr,
    lead_id: NotBlankStr | None = None,
) -> tuple[NotBlankStr, ...]:
    """Return the agent ids that worked *project_id*, plus its lead.

    A task still waiting in the queue contributes nobody: its assignee is
    dropped via :data:`NEVER_RAN_STATES`. The complement is deliberately
    generous, because a run that failed, was interrupted or was cancelled
    partway is work somebody did, and the retrospective that reads this wants
    those most of all.

    Args:
        task_repo: The task store the assignments are read from.
        project_id: The initiative to scan.
        lead_id: The project's recorded lead, included even when it never
            took a task of its own: leading the initiative is contributing
            to it.

    Returns:
        Sorted, deduplicated agent ids. Empty only when nobody ever started a
        task and no lead is recorded.
    """
    ids: set[NotBlankStr] = set()
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded by project task count
    while True:
        page = await task_repo.query(
            TaskFilterSpec(project=project_id),
            limit=_TASK_PAGE_SIZE,
            offset=offset,
        )
        ids.update(
            task.assigned_to
            for task in page
            if task.assigned_to and task.status not in NEVER_RAN_STATES
        )
        if len(page) < _TASK_PAGE_SIZE:
            break
        offset += _TASK_PAGE_SIZE
    if lead_id is not None:
        ids.add(lead_id)
    return tuple(sorted(ids))


async def contributors_or_empty(
    task_repo: TaskRepository | None,
    *,
    project_id: NotBlankStr | None,
    lead_id: NotBlankStr | None = None,
    failure_event: str,
) -> tuple[NotBlankStr, ...]:
    """Read contributors where the answer is a preference, not a gate.

    Selection prefers a holder who already worked the initiative, and a
    momentarily unavailable task store must cost that preference rather than
    the selection. Callers that need the list to be right (the retrospective,
    which decides where a learning lands) call
    :func:`initiative_contributors` directly and let the failure surface.

    Args:
        task_repo: The task store, or ``None`` when unwired.
        project_id: The initiative to scan, or ``None`` when the work has no
            project.
        lead_id: The project's recorded lead, when known.
        failure_event: The caller's observability event for a failed read,
            so the log names the consumer that was choosing.

    Returns:
        The contributors, or an empty tuple when there is nothing to read or
        the read failed.
    """
    if task_repo is None or project_id is None:
        return ()
    try:
        return await initiative_contributors(
            task_repo, project_id=project_id, lead_id=lead_id
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- costs a preference, never the selection
        reraise_critical(exc)
        logger.warning(
            failure_event,
            project_id=project_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="choosing org-wide instead of preferring the initiative's own",
        )
        return ()


__all__ = ["contributors_or_empty", "initiative_contributors"]
