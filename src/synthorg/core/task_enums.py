"""Task-family enumerations and stakes ordering."""

from enum import StrEnum


class TaskStatus(StrEnum):
    """Lifecycle status of a task.

    The authoritative transition map lives in
    ``synthorg.core.task_transitions.VALID_TRANSITIONS``.
    Summary for quick reference:

        CREATED -> ASSIGNED | REJECTED
        ASSIGNED -> IN_PROGRESS | AUTH_REQUIRED | BLOCKED | CANCELLED
                    | FAILED | INTERRUPTED | SUSPENDED
        IN_PROGRESS -> IN_REVIEW | AUTH_REQUIRED | BLOCKED | CANCELLED
                       | FAILED | INTERRUPTED | SUSPENDED
        IN_REVIEW -> COMPLETED | IN_PROGRESS (rework) | BLOCKED | CANCELLED
        AUTH_REQUIRED -> ASSIGNED (approved) | CANCELLED (denied/timeout)
        BLOCKED -> ASSIGNED (unblocked)
        FAILED -> ASSIGNED (reassignment for retry)
        INTERRUPTED -> ASSIGNED (reassignment on restart)
        SUSPENDED -> ASSIGNED (resume from checkpoint)
        COMPLETED, CANCELLED, and REJECTED are terminal states.
        FAILED, INTERRUPTED, and SUSPENDED are non-terminal (can be reassigned).
        AUTH_REQUIRED is non-terminal (waiting for authorization).
    """

    CREATED = "created"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth_required"


class TaskType(StrEnum):
    """Classification of the kind of work a task represents."""

    DEVELOPMENT = "development"
    DESIGN = "design"
    RESEARCH = "research"
    REVIEW = "review"
    MEETING = "meeting"
    ADMIN = "admin"
    ANALYSIS = "analysis"


class Priority(StrEnum):
    """Task urgency and importance level."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Complexity(StrEnum):
    """Estimated task complexity."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    EPIC = "epic"


class Stakes(StrEnum):
    """How consequential a subtask or task is for stakes-aware routing.

    Distinct from :class:`Priority` (urgency/importance) and
    :class:`Complexity` (effort): stakes captures the *cost of being
    wrong*. Low-stakes work tolerates a cheap model; high-stakes work
    (architecture, irreversible decisions) warrants a strong model and
    an adversarial red-team review. The authoritative ordering lives in
    ``_STAKES_ORDER`` below.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# Ordering: LOW < NORMAL < HIGH < CRITICAL. Explicit literal (not a
# dynamic tuple(Stakes)) so the sync guard below is not tautological and
# a new member forces a conscious placement here.
_STAKES_ORDER: tuple[Stakes, ...] = (
    Stakes.LOW,
    Stakes.NORMAL,
    Stakes.HIGH,
    Stakes.CRITICAL,
)

# Fail loudly if the ordering tuple drifts from the enum membership.
# The symmetric difference names whichever members are out of sync.
if set(_STAKES_ORDER) != set(Stakes):
    _stakes_msg = f"_STAKES_ORDER out of sync: {set(_STAKES_ORDER) ^ set(Stakes)}"
    raise RuntimeError(_stakes_msg)

_STAKES_RANK: dict[Stakes, int] = {
    level: idx for idx, level in enumerate(_STAKES_ORDER)
}


def compare_stakes(a: Stakes, b: Stakes) -> int:
    """Compare two stakes levels.

    Returns negative if *a* is lower-stakes than *b*, zero if equal,
    positive if *a* is higher-stakes than *b*.

    Args:
        a: First stakes level.
        b: Second stakes level.

    Returns:
        Integer indicating relative stakes.
    """
    return _STAKES_RANK[a] - _STAKES_RANK[b]


class TaskStructure(StrEnum):
    """Classification of how a task's subtasks relate to each other.

    Used by the decomposition engine to determine coordination topology
    and execution ordering. See the Engine design page.
    """

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    MIXED = "mixed"


class CoordinationTopology(StrEnum):
    """Coordination topology for multi-agent task execution.

    Determines how agents coordinate when executing decomposed subtasks.
    See the Engine design page.
    """

    SAS = "sas"
    CENTRALIZED = "centralized"
    DECENTRALIZED = "decentralized"
    CONTEXT_DEPENDENT = "context_dependent"
    AUTO = "auto"


class TaskSource(StrEnum):
    """Origin of a task within the system.

    Distinguishes tasks created internally by agents from those
    originating from client simulation or external API calls.
    """

    INTERNAL = "internal"
    CLIENT = "client"
    SIMULATION = "simulation"
