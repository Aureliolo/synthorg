"""HR feature state slice.

Holds the agent-lifecycle services: the agent registry, performance
tracker, personality service, agent version service, activity feed, and
agent-health service. The registry and performance tracker are
constructor-injected; the rest are wired lazily once persistence is
connected. All fields are ``None``
until wired; readers guard accordingly.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.hr.activity_service import ActivityFeedService
from synthorg.hr.health.service import AgentHealthService
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.identity.version_service import AgentVersionService
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.personalities.service import PersonalityService
from synthorg.hr.pruning.service import PruningService
from synthorg.hr.registry import AgentRegistryService


class HrStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the HR feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_registry: AgentRegistryService | None = None
    performance_tracker: PerformanceTracker | None = None
    personality_service: PersonalityService | None = None
    agent_version_service: AgentVersionService | None = None
    activity_feed_service: ActivityFeedService | None = None
    agent_health_service: AgentHealthService | None = None
    hiring_service: HiringService | None = None
    pruning_service: PruningService | None = None


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


def hiring_service_of(app_state: AppStateSliceMixin) -> HiringService:
    """Resolve the hiring service from its slice, or raise 503.

    Returns:
        The wired hiring service.
    """
    return require_service(
        app_state.slice(HrStateSlice).hiring_service, "Hiring Service"
    )
