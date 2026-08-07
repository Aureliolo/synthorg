# module-kind: code
"""Declarative subsystem model.

A subsystem declares what it needs and what it makes available; it does not
decide when to run. :mod:`synthorg.api.subsystems.reconciler` compares the
declarations against live state and activates or deactivates accordingly, so
"my dependency was missing at boot" stops being a permanent verdict.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemGraphInvalidError


class CapabilityId(StrEnum):
    """A named thing a subsystem can require or make available.

    The identifier is the join between a producer and its consumers, so the
    reconciler can order activation without anyone hand-maintaining a
    sequence, and can name the missing dependency when one is absent.
    """

    # Ambient preconditions: established during construction, before any
    # subsystem runs. Nothing declares them, so they never block ordering;
    # they are here because a subsystem still needs to name what it waits on.
    PERSISTENCE = "persistence"
    SETTINGS_RESOLVER = "settings_resolver"
    SETTINGS_SERVICE = "settings_service"
    PROVIDER_REGISTRY = "provider_registry"
    COST_TRACKER = "cost_tracker"
    APPROVAL_STORE = "approval_store"
    MESSAGE_BUS = "message_bus"
    AGENT_REGISTRY = "agent_registry"
    WORK_PIPELINE = "work_pipeline"
    TASK_ENGINE = "task_engine"
    COORDINATOR = "coordinator"
    WORKSPACE_SERVICE = "workspace_service"
    SETTINGS_READ_SERVICE = "settings_read_service"

    # Owned by a declared subsystem.
    MEMORY_BACKEND = "memory_backend"
    ORG_MEMORY_BACKEND = "org_memory_backend"
    EVOLUTION_OUTCOMES = "evolution_outcomes"
    DOCS_ENGINE = "docs_engine"
    RESEARCH_ENGINE = "research_engine"
    KNOWLEDGE_ENGINE = "knowledge_engine"
    PROJECT_BRAIN = "project_brain"
    CHARTER_ENGINE = "charter_engine"
    TOOLSMITH = "toolsmith"
    MODEL_REFRESH = "model_refresh"
    OPERATOR_CONSOLE = "operator_console"
    CONVERSATIONAL_ACTOR = "conversational_actor"
    CHIEF_OF_STAFF_CHAT = "chief_of_staff_chat"
    CHIEF_OF_STAFF_PROPOSER = "chief_of_staff_proposer"
    TURN_INTENT_CLASSIFIER = "turn_intent_classifier"
    MULTI_VOICE_ROUTER = "multi_voice_router"
    GROUP_CHAT = "group_chat"
    SIGNALS_SERVICE = "signals_service"
    CUSTOM_RULES = "custom_rules"
    SELF_IMPROVEMENT = "self_improvement"
    ANALYTICS_SERVICE = "analytics_service"
    REPORTS_SERVICE = "reports_service"
    EXPERIMENT_SERVICE = "experiment_service"
    AB_TEST_REPO = "ab_test_repo"
    ALERT_REPO = "alert_repo"
    ORG_INFLECTION_MONITOR = "org_inflection_monitor"
    SPRINT_SERVICE = "sprint_service"
    RISK_OVERRIDE_SERVICE = "risk_override_service"
    DELIVERABLE_RECEIPTS = "deliverable_receipts"
    TOOL_CALL_FEEDBACK = "tool_call_feedback"
    ROLE_VERSION_SERVICE = "role_version_service"
    BUDGET_VERSIONS_SERVICE = "budget_versions_service"
    PROJECT_ROLLUP_SERVICE = "project_rollup_service"
    # One per tail collaborator, not one for the tail: they need different
    # things and converge at different times, so a single capability made the
    # union of their requirements a precondition for any of them and a boot
    # without a coordinator got no integrate stage either.
    INITIATIVE_INTEGRATE = "initiative_integrate"
    INITIATIVE_EVALUATE = "initiative_evaluate"
    INITIATIVE_REPLAN = "initiative_replan"
    INITIATIVE_RETRO_CAPTURE = "initiative_retro_capture"
    KANBAN_BOARD = "kanban_board"
    STEERING_SERVICE = "steering_service"
    FINE_TUNE_ORCHESTRATOR = "fine_tune_orchestrator"
    TEAM_SERVICE = "team_service"
    COMPANY_READ_SERVICE = "company_read_service"
    PLAN_ITEM_REPLY_SERVICE = "plan_item_reply_service"
    ANALYTICS_COLLECTOR = "analytics_collector"
    EVAL_LOOP = "eval_loop"
    PRUNING_SERVICE = "pruning_service"
    SCALING_SERVICE = "scaling_service"
    QUOTA_POLLER = "quota_poller"
    STRATEGY_CONTEXT = "strategy_context"
    RUN_NARRATOR = "run_narrator"
    REFINEMENT_ROUTER = "refinement_router"
    PLAN_REVIEW_GATE = "plan_review_gate"
    PLAN_REVIEW_PANEL = "plan_review_panel"
    CONVERSATIONAL_PLAN_DISPATCHER = "conversational_plan_dispatcher"


class SubsystemPhase(StrEnum):
    """What the reconciler last observed about a subsystem.

    ``WAITING`` and ``DISABLED`` are ordinary resting states, not errors: the
    first means a dependency has not arrived yet and the subsystem will come
    up when it does, the second means an operator turned it off.

    ``BLOCKED`` is the honest answer to a case the declarations cannot model:
    every declared dependency is present, activation ran, and the subsystem
    declined anyway on a condition of its own (memory with no embedding model
    chosen). Reporting that as ``WAITING`` would name no dependency and leave
    an operator with nowhere to look; the subsystem logs the reason.

    ``DEGRADED`` is up while a requirement it captured is gone. Only a
    subsystem with no ``deactivate`` can rest here: one with a teardown is
    taken down instead. Reporting it as ``ACTIVE`` would claim a collaborator
    that is not there, which is the drift reading liveness from ``provides``
    exists to prevent.

    ``UNREACHABLE`` is ``WAITING`` that waiting alone will not resolve.
    Level-triggering rests on "a dependency absent at boot is not a verdict:
    the next pass picks it up", which holds for a dependency that is merely
    late and not for one an operator switched off or that declined on its own
    condition. Reporting that as ``WAITING`` promises a pass that will change
    nothing. It is re-derived every pass, so the operator action that fixes
    the owner clears it on the next one; what it says is "this needs a
    change, not more time".

    ``REBUILDING`` is the window inside a pass where a subsystem has been torn
    down and not yet brought back. It reads as neither up nor waiting-on-
    anything, and a concurrent read answering ``WAITING`` with an empty
    ``waiting_on`` is the contract's own shape used to say nothing.
    """

    ACTIVE = "active"
    DEGRADED = "degraded"
    WAITING = "waiting"
    UNREACHABLE = "unreachable"
    REBUILDING = "rebuilding"
    BLOCKED = "blocked"
    DISABLED = "disabled"
    FAILED = "failed"


#: Phases that name an unmet requirement, so may carry ``waiting_on``.
#: Beside the enum rather than in the status model, so a ninth phase is
#: classified where it is declared instead of somewhere that has to be
#: remembered.
PHASES_NAMING_UNMET: Final[frozenset[SubsystemPhase]] = frozenset(
    {SubsystemPhase.WAITING, SubsystemPhase.UNREACHABLE, SubsystemPhase.DEGRADED}
)

#: Phases that have something to explain, so may carry ``detail``.
PHASES_WITH_DETAIL: Final[frozenset[SubsystemPhase]] = frozenset(
    {SubsystemPhase.FAILED, SubsystemPhase.BLOCKED, SubsystemPhase.UNREACHABLE}
)


type Activate = Callable[[AppState], Awaitable[None]]
type Deactivate = Callable[[AppState], Awaitable[None]]
type Present = Callable[[AppState], bool]


@dataclass(frozen=True, slots=True)
class Capability:
    """A capability and the live check for whether it is available.

    Attributes:
        id: The identifier subsystems reference in ``requires`` / ``provides``.
        present: Reads live state and reports whether the capability is there.
            Called on every reconcile, so it must be cheap and must not raise.
    """

    id: CapabilityId
    present: Present


@dataclass(frozen=True, slots=True)
class SubsystemSpec:
    """One subsystem, declared rather than sequenced.

    ``activate`` is the wiring function itself. ``requires`` states the
    dependency check where the reconciler can read it, order by it, and report
    on it, rather than leaving it inside the function body where only that one
    call site can act on it.

    Attributes:
        name: Stable operator-facing identifier, also the status-surface key.
        provides: The capability that exists once this subsystem is up.
            Doubles as the liveness check, so no separate "is it wired"
            predicate can drift from what activation actually installs.
        requires: Capabilities that must be available before activation.
        activate: Brings the subsystem up. Must be idempotent: the reconciler
            is level-triggered and will call it again on any pass where the
            subsystem reads as not yet active.
        deactivate: Takes the subsystem down when a dependency it captured at
            activation goes away. ``None`` means the subsystem has no teardown
            and is left alone.
        enabled_by: ``namespace.key`` of a boolean setting gating this
            subsystem. ``None`` means always enabled.
        settings: ``namespace.key`` settings activation reads. Declared here so
            the settings subscriber derives what to watch instead of keeping a
            second list, and so a write to one drives a pass. Declaring a
            setting is enough to bring an inactive subsystem up when the value
            it was waiting for arrives; replacing an already-running instance
            additionally needs ``rebuild_on_change``.
        rebuild_on_change: When true, a change in any required capability or
            declared setting deactivates and reactivates rather than leaving
            the running instance alone. Needed where activation captures a
            dependency by value (the engine reads the memory slice once, at
            construction), which is OSGi's static-reference policy. Requires
            ``deactivate``: without a teardown there is nothing to rebuild
            from, so the declaration is refused rather than silently ignored.
    """

    name: str
    provides: CapabilityId
    activate: Activate
    requires: tuple[CapabilityId, ...] = ()
    deactivate: Deactivate | None = None
    enabled_by: str | None = None
    settings: tuple[str, ...] = ()
    rebuild_on_change: bool = False

    def __post_init__(self) -> None:
        """Refuse a declaration that cannot keep its own promise.

        Checked here rather than in the graph because it needs nothing but
        this object, and a declaration site is where the author can see what
        went wrong.

        Raises:
            SubsystemGraphInvalidError: When ``rebuild_on_change`` is declared
                with no ``deactivate``: rebuilding is teardown-then-activate,
                so without a teardown the subsystem still reads active, the
                pass leaves it alone, and the promise never fires. The same
                error the graph raises for its mirror fault, a replaceable
                dependency whose consumer declares no rebuild.
        """
        if self.rebuild_on_change and self.deactivate is None:
            msg = (
                f"Subsystem {self.name!r} declares rebuild_on_change with no "
                "deactivate; a rebuild needs a teardown to rebuild from"
            )
            raise SubsystemGraphInvalidError(msg)
