# module-kind: code
"""What a sprint scope is, and who is allowed to open one in it.

A sprint belongs either to a project or to the org as a whole, and the two
are different scopes. That distinction is easy to lose, because the filter
spec's unset ``project`` means "no project predicate" rather than "the
org-wide scope", so a question about the org silently answers about
somebody else's sprint unless it is asked by name. It is asked by name in
exactly one place, here.

Beside it lives the rule the scope enforces: one non-completed sprint at a
time. The service checks it before writing and the database's partial
unique index decides it, and both refusals reach the caller as the same
error, so the answer does not depend on which of the two got there first.
"""

from uuid import uuid4

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import SprintAlreadyOpenError
from synthorg.engine.workflow.sprint_config import SprintConfig
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import SPRINT_REFUSED
from synthorg.persistence.sprint_protocol import SprintFilterSpec, SprintRepository

logger = get_logger(__name__)


def log_refusal(*, reason: str, **context: object) -> None:
    """Record that the sprint surface is about to refuse a caller.

    Every refusal it makes calls this immediately before its ``raise``, so
    a caller reporting a 409 is findable in the log without reproducing it,
    and so no branch is left as the one that refuses in silence. Kept
    beside the ``raise`` rather than wrapped around it, so the statement
    that ends the call still names the error it ends with.

    Args:
        reason: A short slug naming which branch refused.
        **context: Extra structured fields for the log.
    """
    logger.info(SPRINT_REFUSED, reason=reason, **context)


def scope_spec(project: str | None) -> SprintFilterSpec:
    """Build the filter naming one sprint scope.

    ``project=None`` on its own means "no project predicate", which matches
    every scope, so the org-wide scope has to be asked for by name or a
    question about it silently answers about somebody else's sprint.

    Args:
        project: The owning project, or ``None`` for the org-wide scope.

    Returns:
        The spec matching exactly the requested scope.
    """
    if project is None:
        return SprintFilterSpec(org_wide_only=True)
    return SprintFilterSpec(project=NotBlankStr(project))


async def scope_occupant(
    project: str | None, *, sprints: SprintRepository
) -> Sprint | None:
    """Return the sprint holding *project*'s scope, if one does.

    The one place "is this scope taken" is asked. Its two callers want
    different things from the answer (a refusal the operator reads, and a
    quiet return on the auto-create path), which is exactly how one
    predicate comes to be written twice and then to drift.

    Args:
        project: The scope being asked about.
        sprints: The durable store.

    Returns:
        The occupying non-completed sprint, or ``None`` when the scope is
        free.
    """
    for existing in await sprints.query(scope_spec(project)):
        if existing.status is not SprintStatus.COMPLETED:
            return existing
    return None


async def require_scope_free(project: str | None, *, sprints: SprintRepository) -> None:
    """Refuse when *project*'s scope already carries an open sprint.

    Args:
        project: The scope being opened.
        sprints: The durable store, read for the occupier.

    Raises:
        SprintAlreadyOpenError: When a non-completed sprint exists, naming
            it so the caller can go and finish it.
    """
    existing = await scope_occupant(project, sprints=sprints)
    if existing is not None:
        log_refusal(reason="scope_occupied", project=project, sprint_id=existing.id)
        raise SprintAlreadyOpenError(
            sprint_name=existing.name,
            sprint_id=existing.id,
            sprint_status=existing.status.value,
        )


def scope_occupied_error(project: str | None) -> SprintAlreadyOpenError:
    """Build the refusal for a scope another writer claimed first.

    Args:
        project: The scope that was claimed.

    Returns:
        The error to raise, carrying no occupier: this process never read
        the winning row, and naming a sprint it did not see would be an
        invention rather than an answer.
    """
    scope = f"project {project!r}" if project is not None else "the org"
    log_refusal(reason="scope_claimed_by_another_writer", project=project)
    return SprintAlreadyOpenError(
        f"Another writer opened a sprint for {scope} first; "
        f"finish it before starting another"
    )


async def build_planning_sprint(
    project: str | None, *, sprints: SprintRepository, config: SprintConfig
) -> Sprint:
    """Construct a fresh ``PLANNING`` sprint with the next number.

    Args:
        project: The owning scope.
        sprints: The durable store, read for the scope's highest number.
        config: Supplies the sprint duration.

    Returns:
        An unsaved PLANNING sprint numbered after the scope's latest.
    """
    existing = await sprints.query(scope_spec(project))
    number = 1 + max((s.sprint_number for s in existing), default=0)
    return Sprint(
        id=NotBlankStr(str(uuid4())),
        project=NotBlankStr(project) if project is not None else None,
        name=NotBlankStr(f"Sprint {number}"),
        sprint_number=number,
        duration_days=config.duration_days,
    )


__all__ = [
    "build_planning_sprint",
    "log_refusal",
    "require_scope_free",
    "scope_occupant",
    "scope_occupied_error",
    "scope_spec",
]
