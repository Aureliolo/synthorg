"""HR feature state slice.

Holds the agent-lifecycle services: the agent registry, performance
tracker, training service + plan service, personality service, agent
version service, activity feed, agent-health service, and the scaling
service + decision service. The registry / performance tracker /
training service are constructor-injected; the rest are wired lazily
once persistence is connected. All fields are ``None`` until wired;
readers guard accordingly.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.hr.activity_service import ActivityFeedService  # noqa: TC001
from synthorg.hr.health.service import AgentHealthService  # noqa: TC001
from synthorg.hr.identity.version_service import AgentVersionService  # noqa: TC001
from synthorg.hr.performance.tracker import PerformanceTracker  # noqa: TC001
from synthorg.hr.personalities.service import PersonalityService  # noqa: TC001
from synthorg.hr.registry import AgentRegistryService  # noqa: TC001
from synthorg.hr.scaling.decision_service import (
    ScalingDecisionService,  # noqa: TC001
)
from synthorg.hr.scaling.service import ScalingService  # noqa: TC001
from synthorg.hr.training.plan_service import TrainingPlanService  # noqa: TC001
from synthorg.hr.training.service import TrainingService  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class HrStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the HR feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_registry: AgentRegistryService | None = None
    performance_tracker: PerformanceTracker | None = None
    training_service: TrainingService | None = None
    training_plan_service: TrainingPlanService | None = None
    personality_service: PersonalityService | None = None
    agent_version_service: AgentVersionService | None = None
    activity_feed_service: ActivityFeedService | None = None
    agent_health_service: AgentHealthService | None = None
    scaling_service: ScalingService | None = None
    scaling_decision_service: ScalingDecisionService | None = None


def agent_registry_of(app_state: AppStateSliceMixin) -> AgentRegistryService:
    """Resolve the agent registry from its slice, or raise 503.

    Returns:
        The wired agent registry service.
    """
    return require_service(
        app_state.slice(HrStateSlice).agent_registry, "Agent Registry"
    )


def performance_tracker_of(app_state: AppStateSliceMixin) -> PerformanceTracker:
    """Resolve the performance tracker from its slice, or raise 503.

    Returns:
        The wired performance tracker.
    """
    return require_service(
        app_state.slice(HrStateSlice).performance_tracker, "Performance Tracker"
    )


def training_service_of(app_state: AppStateSliceMixin) -> TrainingService:
    """Resolve the training service from its slice, or raise 503.

    Returns:
        The wired training service.
    """
    return require_service(
        app_state.slice(HrStateSlice).training_service, "Training Service"
    )


def personality_service_of(app_state: AppStateSliceMixin) -> PersonalityService:
    """Resolve the personality service from its slice, or raise 503.

    Returns:
        The wired personality service.
    """
    return require_service(
        app_state.slice(HrStateSlice).personality_service, "Personality Service"
    )


def agent_version_service_of(app_state: AppStateSliceMixin) -> AgentVersionService:
    """Resolve the agent version service from its slice, or raise 503.

    Returns:
        The wired agent version service.
    """
    return require_service(
        app_state.slice(HrStateSlice).agent_version_service, "Agent Version Service"
    )


def activity_feed_service_of(app_state: AppStateSliceMixin) -> ActivityFeedService:
    """Resolve the activity feed service from its slice, or raise 503.

    Returns:
        The wired activity feed service.
    """
    return require_service(
        app_state.slice(HrStateSlice).activity_feed_service, "Activity Feed Service"
    )


def agent_health_service_of(app_state: AppStateSliceMixin) -> AgentHealthService:
    """Resolve the agent health service from its slice, or raise 503.

    Returns:
        The wired agent health service.
    """
    return require_service(
        app_state.slice(HrStateSlice).agent_health_service, "Agent Health Service"
    )


def scaling_decision_service_of(
    app_state: AppStateSliceMixin,
) -> ScalingDecisionService:
    """Resolve the scaling decision service from its slice, or raise 503.

    Returns:
        The wired scaling decision service.
    """
    return require_service(
        app_state.slice(HrStateSlice).scaling_decision_service,
        "Scaling Decision Service",
    )
