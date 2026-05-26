"""AppState facade accessors for observability / memory / coordination services.

Provides accessors for the eight MCP-facing services spanning the
observability, memory, and coordination domains (activity feed,
agent health, agent versions, personalities, scaling decisions,
coordination metrics, ceremony policy, and the memory service).
Kept in its own module so the parent ``state_services_facades.py``
stays under the project's 800-line ceiling. Each service follows
the same triple:

- ``has_<service>`` -- ``bool`` indicating whether the setter has run.
- ``<service>`` -- the service itself (raises via
  ``_require_service`` when not wired).
- ``set_<service>`` -- one-shot setter enforced by ``_set_once``.

Each setter emits an INFO-level audit event
(``API_STATE_SERVICE_ATTACHED``) with the slot name and the service's
concrete class so ops telemetry can observe bootstrap wiring.
"""

from typing import Any

from synthorg.coordination.ceremony_policy.service import (
    CeremonyPolicyService,  # noqa: TC001
)
from synthorg.coordination.service import CoordinationService  # noqa: TC001
from synthorg.hr.activity_service import ActivityFeedService  # noqa: TC001
from synthorg.hr.health.service import AgentHealthService  # noqa: TC001
from synthorg.hr.identity.version_service import AgentVersionService  # noqa: TC001
from synthorg.hr.personalities.service import PersonalityService  # noqa: TC001
from synthorg.hr.scaling.decision_service import (
    ScalingDecisionService,  # noqa: TC001
)
from synthorg.memory.service import MemoryService  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_STATE_SERVICE_ATTACHED

logger = get_logger(__name__)


class _MetaMcp4FacadesMixin:
    """Facade accessors for the eight META-MCP-4 services.

    This mixin is composed into :class:`_FacadesMixin` (and thus into
    :class:`AppStateServicesMixin`) so concrete :class:`AppState`
    instances expose the full facade surface without pushing the
    parent module over the 800-line ceiling.
    """

    _set_once: Any

    def _require_service[T](  # pragma: no cover
        self, service: T | None, name: str
    ) -> T:
        """Return *service* or raise (implemented on concrete ``AppState``).

        Forwards to the concrete implementation declared by
        :class:`AppState` so the mixin can call ``_require_service``
        from every property below.
        """
        raise NotImplementedError

    def _attach_service(
        self,
        *,
        slot: str,
        service: Any,
        name: str,
    ) -> None:
        """Run ``_set_once`` then emit the INFO audit event.

        Logging must happen *after* the slot is set so a duplicate
        attach (which ``_set_once`` rejects by raising) does not
        produce a misleading ``API_STATE_SERVICE_ATTACHED`` event.
        """
        self._set_once(slot, service, name)
        logger.info(
            API_STATE_SERVICE_ATTACHED,
            slot=slot,
            service_class=type(service).__name__,
        )

    # Slot attrs (declared on the concrete AppState; redeclared here
    # so mypy narrows access through the mixin).
    _activity_feed_service: ActivityFeedService | None
    _agent_health_service: AgentHealthService | None
    _agent_version_service: AgentVersionService | None
    _ceremony_policy_service: CeremonyPolicyService | None
    _coordination_service: CoordinationService | None
    _memory_service: MemoryService | None
    _personality_service: PersonalityService | None
    _scaling_decision_service: ScalingDecisionService | None

    # ── ActivityFeedService ──────────────────────────────────────

    @property
    def has_activity_feed_service(self) -> bool:
        """Whether the activity-feed service has been attached.

        Returns:
            ``True`` once :meth:`set_activity_feed_service` has run.
        """
        return self._activity_feed_service is not None

    @property
    def activity_feed_service(self) -> ActivityFeedService:
        """Return the attached :class:`ActivityFeedService`.

        Returns:
            The attached service.

        Raises:
            RuntimeError: When the service has not been wired yet.
        """
        return self._require_service(
            self._activity_feed_service,
            "activity_feed_service",
        )

    def set_activity_feed_service(
        self,
        service: ActivityFeedService,
    ) -> None:
        """Attach the activity-feed service (one-shot).

        Args:
            service: The :class:`ActivityFeedService` instance wired
                by the application bootstrap.

        Raises:
            RuntimeError: If a service has already been attached to
                this slot.
        """
        self._attach_service(
            slot="_activity_feed_service",
            service=service,
            name="activity_feed_service",
        )

    # ── AgentHealthService ───────────────────────────────────────

    @property
    def has_agent_health_service(self) -> bool:
        """Whether the agent-health service has been attached.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._agent_health_service is not None

    @property
    def agent_health_service(self) -> AgentHealthService:
        """Return the attached :class:`AgentHealthService`.

        Raises:
            RuntimeError: When the service has not been wired yet.

        Returns:
            ``AgentHealthService`` instance.
        """
        return self._require_service(
            self._agent_health_service,
            "agent_health_service",
        )

    def set_agent_health_service(
        self,
        service: AgentHealthService,
    ) -> None:
        """Attach the agent-health service (one-shot).

        Args:
            service: The :class:`AgentHealthService` instance.

        Raises:
            RuntimeError: If already attached.
        """
        self._attach_service(
            slot="_agent_health_service",
            service=service,
            name="agent_health_service",
        )

    # ── AgentVersionService ──────────────────────────────────────

    @property
    def has_agent_version_service(self) -> bool:
        """Whether the agent-version service has been attached.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._agent_version_service is not None

    @property
    def agent_version_service(self) -> AgentVersionService:
        """Return the attached :class:`AgentVersionService`.

        Raises:
            RuntimeError: When the service has not been wired yet.

        Returns:
            ``AgentVersionService`` instance.
        """
        return self._require_service(
            self._agent_version_service,
            "agent_version_service",
        )

    def set_agent_version_service(
        self,
        service: AgentVersionService,
    ) -> None:
        """Attach the agent-version service (one-shot).

        Args:
            service: The :class:`AgentVersionService` instance.

        Raises:
            RuntimeError: If already attached.
        """
        self._attach_service(
            slot="_agent_version_service",
            service=service,
            name="agent_version_service",
        )

    # ── CeremonyPolicyService ────────────────────────────────────

    @property
    def has_ceremony_policy_service(self) -> bool:
        """Whether the ceremony-policy service has been attached.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._ceremony_policy_service is not None

    @property
    def ceremony_policy_service(self) -> CeremonyPolicyService:
        """Return the attached :class:`CeremonyPolicyService`.

        Raises:
            RuntimeError: When the service has not been wired yet.

        Returns:
            ``CeremonyPolicyService`` instance.
        """
        return self._require_service(
            self._ceremony_policy_service,
            "ceremony_policy_service",
        )

    def set_ceremony_policy_service(
        self,
        service: CeremonyPolicyService,
    ) -> None:
        """Attach the ceremony-policy service (one-shot).

        Args:
            service: The :class:`CeremonyPolicyService` instance.

        Raises:
            RuntimeError: If already attached.
        """
        self._attach_service(
            slot="_ceremony_policy_service",
            service=service,
            name="ceremony_policy_service",
        )

    # ── CoordinationService ──────────────────────────────────────

    @property
    def has_coordination_service(self) -> bool:
        """Whether the coordination service has been attached.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._coordination_service is not None

    @property
    def coordination_service(self) -> CoordinationService:
        """Return the attached :class:`CoordinationService`.

        Raises:
            RuntimeError: When the service has not been wired yet.

        Returns:
            ``CoordinationService`` instance.
        """
        return self._require_service(
            self._coordination_service,
            "coordination_service",
        )

    def set_coordination_service(
        self,
        service: CoordinationService,
    ) -> None:
        """Attach the coordination service (one-shot).

        Args:
            service: The :class:`CoordinationService` instance.

        Raises:
            RuntimeError: If already attached.
        """
        self._attach_service(
            slot="_coordination_service",
            service=service,
            name="coordination_service",
        )

    # ── MemoryService ────────────────────────────────────────────

    @property
    def has_memory_service(self) -> bool:
        """Whether the memory service has been attached.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._memory_service is not None

    @property
    def memory_service(self) -> MemoryService:
        """Return the attached :class:`MemoryService`.

        Raises:
            RuntimeError: When the service has not been wired yet.

        Returns:
            ``MemoryService`` instance.
        """
        return self._require_service(
            self._memory_service,
            "memory_service",
        )

    def set_memory_service(
        self,
        service: MemoryService,
    ) -> None:
        """Attach the memory service (one-shot).

        Args:
            service: The :class:`MemoryService` instance.

        Raises:
            RuntimeError: If already attached.
        """
        self._attach_service(
            slot="_memory_service",
            service=service,
            name="memory_service",
        )

    # ── PersonalityService ───────────────────────────────────────

    @property
    def has_personality_service(self) -> bool:
        """Whether the personality service has been attached.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._personality_service is not None

    @property
    def personality_service(self) -> PersonalityService:
        """Return the attached :class:`PersonalityService`.

        Raises:
            RuntimeError: When the service has not been wired yet.

        Returns:
            ``PersonalityService`` instance.
        """
        return self._require_service(
            self._personality_service,
            "personality_service",
        )

    def set_personality_service(
        self,
        service: PersonalityService,
    ) -> None:
        """Attach the personality service (one-shot).

        Args:
            service: The :class:`PersonalityService` instance.

        Raises:
            RuntimeError: If already attached.
        """
        self._attach_service(
            slot="_personality_service",
            service=service,
            name="personality_service",
        )

    # ── ScalingDecisionService ───────────────────────────────────

    @property
    def has_scaling_decision_service(self) -> bool:
        """Whether the scaling-decision service has been attached.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._scaling_decision_service is not None

    @property
    def scaling_decision_service(self) -> ScalingDecisionService:
        """Return the attached :class:`ScalingDecisionService`.

        Raises:
            RuntimeError: When the service has not been wired yet.

        Returns:
            ``ScalingDecisionService`` instance.
        """
        return self._require_service(
            self._scaling_decision_service,
            "scaling_decision_service",
        )

    def set_scaling_decision_service(
        self,
        service: ScalingDecisionService,
    ) -> None:
        """Attach the scaling-decision service (one-shot).

        Args:
            service: The :class:`ScalingDecisionService` instance.

        Raises:
            RuntimeError: If already attached.
        """
        self._attach_service(
            slot="_scaling_decision_service",
            service=service,
            name="scaling_decision_service",
        )


__all__ = ["_MetaMcp4FacadesMixin"]
