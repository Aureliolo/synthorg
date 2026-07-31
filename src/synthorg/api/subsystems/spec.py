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

from synthorg.api.state import AppState


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
    DEFAULT_PROVIDER = "default_provider"
    COST_TRACKER = "cost_tracker"
    APPROVAL_STORE = "approval_store"
    MESSAGE_BUS = "message_bus"
    AGENT_REGISTRY = "agent_registry"
    WORK_PIPELINE = "work_pipeline"
    TASK_ENGINE = "task_engine"
    WORKSPACE_SERVICE = "workspace_service"

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
    SETTINGS_READ_SERVICE = "settings_read_service"


class SubsystemPhase(StrEnum):
    """What the reconciler last observed about a subsystem.

    ``WAITING`` and ``DISABLED`` are ordinary resting states, not errors: the
    first means a dependency has not arrived yet and the subsystem will come
    up when it does, the second means an operator turned it off.
    """

    ACTIVE = "active"
    WAITING = "waiting"
    DISABLED = "disabled"
    FAILED = "failed"


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

    ``activate`` is the existing wiring function unchanged. What is new is
    ``requires``: the check that used to be buried inside the function body,
    hoisted where the reconciler can read it, order by it, and report on it.

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
        rebuild_on_change: When true, a change in any required capability
            deactivates and reactivates rather than leaving the running
            instance alone. Needed where activation captures a dependency by
            value (the engine reads the memory slice once, at construction),
            which is OSGi's static-reference policy.
    """

    name: str
    provides: CapabilityId
    activate: Activate
    requires: tuple[CapabilityId, ...] = ()
    deactivate: Deactivate | None = None
    enabled_by: str | None = None
    rebuild_on_change: bool = False
