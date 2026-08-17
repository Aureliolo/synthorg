"""Task-family enumerations and stakes ordering."""

from enum import StrEnum
from typing import Final


class TaskStatus(StrEnum):
    """Lifecycle status of a task.

    The authoritative transition map lives in
    ``synthorg.core.task_transitions.VALID_TRANSITIONS``.
    Summary for quick reference:

        CREATED -> ASSIGNED | BLOCKED (nobody to route it to) | REJECTED
                   | FAILED (planning failed before assignment)
        ASSIGNED -> IN_PROGRESS | AUTH_REQUIRED | BLOCKED | CANCELLED
                    | FAILED | INTERRUPTED | SUSPENDED
        IN_PROGRESS -> IN_REVIEW | AWAITING_INPUT | AUTH_REQUIRED | BLOCKED
                       | CANCELLED | FAILED | INTERRUPTED | SUSPENDED
        IN_REVIEW -> COMPLETED | IN_PROGRESS (rework) | BLOCKED | CANCELLED
        AWAITING_INPUT -> IN_PROGRESS (answer supplied) | CANCELLED (abandoned)
        AUTH_REQUIRED -> ASSIGNED (approved) | CANCELLED (denied/timeout)
        BLOCKED -> ASSIGNED (unblocked) | IN_REVIEW (an escalated review's
                   answer rejoins it) | CANCELLED (abandoned)
        FAILED -> ASSIGNED (reassignment for retry) | CANCELLED (abandoned)
        INTERRUPTED -> ASSIGNED (reassignment on restart) | CANCELLED
        SUSPENDED -> ASSIGNED (resume from checkpoint) | CANCELLED
        COMPLETED, CANCELLED, and REJECTED are terminal states.
        FAILED, INTERRUPTED, and SUSPENDED are non-terminal (can be reassigned).
        AUTH_REQUIRED and AWAITING_INPUT are non-terminal (waiting on a human).
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
    AWAITING_INPUT = "awaiting_input"


class BlockedReason(StrEnum):
    """Why a task is parked at :attr:`TaskStatus.BLOCKED`.

    ``BLOCKED`` is reached from several directions that mean different
    things, and the status alone cannot tell them apart. A completion
    review that escalates parks the task for a human; a coordination wave
    releasing a subtask nobody will run parks it for a scheduler. Reading
    the status alone, a rule written for the first silently applies to the
    second, which is how a task blocked by a wave release came to skip the
    verification the review gate exists to impose.

    Absent (``None``) means the writer did not say. It is not a synonym for
    any member here, and no rule may treat it as one.
    """

    ORACLE_ESCALATED = "oracle_escalated"
    WAVE_RELEASED = "wave_released"
    #: Nobody in the org holds the role the completion gate needed, so the
    #: review never happened. Distinct from ORACLE_ESCALATED because the two
    #: are answered by different people: an escalation waits on a human's
    #: decision and must not be re-judged, while this waits on staffing and
    #: MUST be re-judged the moment somebody holds the role.
    REVIEWER_UNSTAFFED = "reviewer_unstaffed"
    #: The same condition on the adversarial gate. Kept apart from
    #: REVIEWER_UNSTAFFED because the two name different roles, and a park
    #: that cannot say which role it waits on gives the staffing sweep
    #: nothing to watch for.
    RED_TEAM_UNSTAFFED = "red_team_unstaffed"
    #: Routing found nobody the work could go to: no agent the stakes admit,
    #: at any rung, scored above the floor. The work is still wanted and the
    #: row is still good, so it parks rather than failing, and it waits on an
    #: operator (hire, re-bind a model, or revise the plan item) rather than on
    #: a sweep. Distinct from WAVE_RELEASED, which is a subtask that WAS routed
    #: and lost its wave.
    NO_CAPABLE_AGENT = "no_capable_agent"
    #: The work this subtask declared it needs did not arrive: an upstream
    #: subtask failed, was cancelled, or is itself parked. Distinct from
    #: WAVE_RELEASED (its own wave could not be assigned, so nothing is wrong
    #: with its inputs) because the two wait on different things: a released
    #: subtask waits on a scheduler, and this one waits on its dependency
    #: being redone, which only a replan can order.
    DEPENDENCY_FAILED = "dependency_failed"


#: Parks that wait on staffing rather than on a person's answer. The
#: review-staffing sweep owns exactly these, and checks its role map against
#: this set at import, so a third gate role cannot ship a park that nothing
#: ever sweeps. ORACLE_ESCALATED, WAVE_RELEASED and NO_CAPABLE_AGENT are
#: deliberately absent: the first waits on a human's decision, the second on a
#: scheduler, and the third on an operator changing the roster, which is not a
#: gate role the sweep can hire for.
STAFFING_BLOCKED_REASONS: Final[frozenset[BlockedReason]] = frozenset(
    {BlockedReason.REVIEWER_UNSTAFFED, BlockedReason.RED_TEAM_UNSTAFFED}
)

#: ``Task.metadata`` key naming the role a ``NO_CAPABLE_AGENT`` park is
#: waiting on. Written when the park happens, because that is the only moment
#: the answer is in hand: the plan asked for a role, routing could not staff
#: it, and the row itself carries no role. The sweep that offers to hire reads
#: it back rather than reopening the plan to re-derive what was already known.
UNROUTABLE_ROLE_KEY: Final[str] = "unroutable_required_role"


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
    """How consequential a subtask or task is for capability-based agent selection.

    Distinct from :class:`Priority` (urgency/importance) and
    :class:`Complexity` (effort): stakes captures the *cost of being
    wrong*. Low-stakes work tolerates a cheap model; high-stakes work
    (architecture, irreversible decisions) warrants a strong model and
    an adversarial red-team review. The authoritative ordering lives in
    ``STAKES_ORDER`` below.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# Ordering: LOW < NORMAL < HIGH < CRITICAL. Explicit literal (not a
# dynamic tuple(Stakes)) so the sync guard below is not tautological and
# a new member forces a conscious placement here.
STAKES_ORDER: tuple[Stakes, ...] = (
    Stakes.LOW,
    Stakes.NORMAL,
    Stakes.HIGH,
    Stakes.CRITICAL,
)

# Fail loudly if the ordering tuple drifts from the enum membership.
# The symmetric difference names whichever members are out of sync.
if set(STAKES_ORDER) != set(Stakes):
    _stakes_msg = f"STAKES_ORDER out of sync: {set(STAKES_ORDER) ^ set(Stakes)}"
    raise RuntimeError(_stakes_msg)

_STAKES_RANK: dict[Stakes, int] = {level: idx for idx, level in enumerate(STAKES_ORDER)}


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


def is_high_stakes(stakes: Stakes) -> bool:
    """Whether *stakes* is at or above HIGH.

    High/critical work must not proceed on a marginal fit: the assignment
    and solo-selection paths reject a below-confidence match at this level
    rather than routing it with a warning.

    Returns:
        ``True`` for HIGH or CRITICAL, ``False`` for LOW or NORMAL.
    """
    return compare_stakes(stakes, Stakes.HIGH) >= 0


class TaskStructure(StrEnum):
    """Classification of how a task's subtasks relate to each other.

    Used by the decomposition engine to determine coordination topology
    and execution ordering. See the Engine design page.

    ``AUTO`` is the unresolved state, not a fourth shape: it says a planner
    declared no structure and something downstream must decide one. It is
    distinct from ``SEQUENTIAL`` precisely so a deliberate sequential
    declaration cannot be mistaken for silence, which is what lets the
    classifier fill only a genuine gap. Nothing accepts it as an answer:
    ``DecompositionResult`` and the plan mapping both refuse it.
    """

    AUTO = "auto"
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
