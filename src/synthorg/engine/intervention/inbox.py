"""Steering inbox: read active steering directives at safe boundaries.

The inbox is the read side of mid-flight steering. It projects the active
steering directives for a project from the project-brain repository's
``list_current`` (a cheap SQL projection, independent of the memory backend),
so an in-flight agent can adopt them at a turn boundary and a freshly-spawned
agent can have them seeded into its initial context.

The write side (recording a directive) goes through ``SteeringService`` and the
full ``ProjectBrainService``; the read side only needs the repository, so the
inbox is available whenever persistence is up.
"""

from typing import Final, Protocol, runtime_checkable

from pydantic import ValidationError

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention.models import (
    STEERING_TAG,
    ActiveSteeringDirective,
    parse_steering_tags,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import STEERING_INBOX_READ_FAILED
from synthorg.persistence.project_brain_protocol import (
    BrainFilterSpec,
    ProjectBrainRepository,
)
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
)

logger = get_logger(__name__)

#: Cap on active steering directives read per safe-boundary projection. A
#: project with more than this many concurrently-active directives is a
#: misuse; the cap bounds the per-turn read cost.
DEFAULT_STEERING_LIMIT: Final[int] = 50


@runtime_checkable
class SteeringInbox(Protocol):
    """Projects the active steering directives an agent should adopt."""

    async def pending(
        self,
        *,
        project_id: str,
        task_id: str | None = None,
        agent_id: str | None = None,
        already_adopted: frozenset[str] = frozenset(),
    ) -> tuple[ActiveSteeringDirective, ...]:
        """Return active directives for the project not yet adopted.

        Args:
            project_id: The owning project.
            task_id: The running task id, for task-narrowing.
            agent_id: The running agent id, for agent-narrowing.
            already_adopted: Brain entry ids this run already adopted.

        Returns:
            Active directives applying to this task/agent, newest-first,
            excluding any already adopted. Best-effort: an empty tuple on a
            read failure (steering never interrupts a healthy loop).
        """
        ...


class BrainBackedSteeringInbox:
    """Default inbox reading active steering directives from the brain repo."""

    def __init__(
        self,
        repo: ProjectBrainRepository,
        *,
        limit: int = DEFAULT_STEERING_LIMIT,
    ) -> None:
        self._repo = repo
        self._limit = limit

    async def pending(
        self,
        *,
        project_id: str,
        task_id: str | None = None,
        agent_id: str | None = None,
        already_adopted: frozenset[str] = frozenset(),
    ) -> tuple[ActiveSteeringDirective, ...]:
        """Project active, applicable, not-yet-adopted directives.

        Returns:
            The directives the running agent should adopt; empty on a
            read failure.
        """
        spec = BrainFilterSpec(
            project_id=NotBlankStr(project_id),
            entry_kind=BrainEntryKind.PLAN_REVISION,
            status=BrainEntryStatus.ACTIVE,
            tag=STEERING_TAG,
        )
        try:
            rows = await self._repo.list_current(spec, limit=self._limit, offset=0)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                STEERING_INBOX_READ_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()

        directives: list[ActiveSteeringDirective] = []
        for entry in rows:
            if entry.entry_id in already_adopted:
                continue
            directive = brain_entry_to_directive(entry)
            if directive is None:
                continue
            if not directive.applies_to(task_id=task_id, agent_id=agent_id):
                continue
            directives.append(directive)
        return tuple(directives)


def brain_entry_to_directive(entry: BrainEntry) -> ActiveSteeringDirective | None:
    """Map a steering brain entry to a typed directive.

    Returns:
        The directive, or ``None`` when the entry carries no recognised
        steering kind tag or fails validation.
    """
    kind, narrow_tasks, narrow_agents = parse_steering_tags(entry.tags)
    if kind is None:
        return None
    try:
        return ActiveSteeringDirective(
            entry_id=entry.entry_id,
            kind=kind,
            text=entry.rationale,
            author=entry.author,
            recorded_at=entry.recorded_at,
            narrow_task_ids=narrow_tasks,
            narrow_agent_ids=narrow_agents,
        )
    except ValidationError:
        return None


def build_steering_inbox(
    repo: ProjectBrainRepository,
    *,
    limit: int = DEFAULT_STEERING_LIMIT,
) -> SteeringInbox:
    """Build the default brain-backed steering inbox.

    Returns:
        A :class:`SteeringInbox` reading from the project-brain repository.
    """
    return BrainBackedSteeringInbox(repo, limit=limit)
