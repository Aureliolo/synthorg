# module-kind: code
"""Turning an approved hire into a live agent.

The three steps between "a human said yes" and "the agent is on the roster":
resolve the pair it will run on, register it, and start its onboarding. They
are free functions taking the collaborator each needs, so the hiring service
keeps the decision flow and the durable request state, and this module keeps
the part that touches the roster.
"""

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.domain_errors import DomainError
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import (
    AgentAlreadyRegisteredError,
    HiringError,
)
from synthorg.hr.models import HiringRequest
from synthorg.hr.onboarding_service import OnboardingService
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_HIRING_ALREADY_REGISTERED,
    HR_HIRING_INSTANTIATED,
    HR_HIRING_INSTANTIATION_FAILED,
    HR_HIRING_MODEL_UNSET,
)
from synthorg.settings.bound_model import resolve_bound_model_live
from synthorg.settings.kill_switch import require_configured_model
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)


async def resolve_new_hire_model(
    config_resolver: ConfigResolverProtocol | None,
) -> ModelConfig:
    """Read the pair a new hire is bound to, refusing an unset one.

    Read live per instantiation rather than captured at wiring, so an
    operator who binds the pair after boot can approve a hire without a
    restart. There is deliberately nothing to fall back to: an agent
    registered against a placeholder provider joins the roster looking
    staffed and fails every dispatch it is ever given.

    Args:
        config_resolver: Reads the live ``hr.new_hire_model`` setting.

    Returns:
        The bound pair the new agent runs on.

    Raises:
        ServiceUnavailableError: When no pair is bound.
    """
    ref = require_configured_model(
        await resolve_bound_model_live(
            config_resolver,
            namespace="hr",
            key="new_hire_model",
            unset_event=HR_HIRING_MODEL_UNSET,
        ),
        namespace="hr",
        key="new_hire_model",
        feature_label="hiring",
    )
    return ModelConfig(
        provider=NotBlankStr(ref.provider),
        model_id=NotBlankStr(ref.model_id),
    )


async def register_agent(
    registry: AgentRegistryService,
    identity: AgentIdentity,
    request: HiringRequest,
) -> None:
    """Register a new agent identity in the registry.

    A collision on THIS request's own id is the request's own earlier
    attempt, not somebody else's agent: the id is derived from the request,
    so nothing else can mint it. That happens when an instantiation is
    interrupted between registering and recording the status, which would
    otherwise strand the request at APPROVED forever while its agent is
    already live and usable. Treating it as done is what makes the retry
    converge.

    Args:
        registry: The roster the agent joins.
        identity: The agent identity to register.
        request: The associated hiring request (for error context).

    Raises:
        HiringError: If registration fails.
    """
    try:
        await registry.register(identity)
    except AgentAlreadyRegisteredError as exc:
        existing = await registry.get(NotBlankStr(str(identity.id)))
        if existing is not None:
            logger.info(
                HR_HIRING_ALREADY_REGISTERED,
                request_id=str(request.id),
                agent_id=str(identity.id),
                note="resuming an interrupted instantiation",
            )
            return
        msg = f"Agent already registered for request {request.id!r}"
        logger.warning(
            HR_HIRING_INSTANTIATION_FAILED,
            request_id=str(request.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise HiringError(msg) from exc


async def try_onboard(
    onboarding_service: OnboardingService | None,
    identity: AgentIdentity,
) -> None:
    """Attempt onboarding if the service is available.

    Onboarding failure is non-fatal: the agent is already registered and can
    be onboarded later.

    Args:
        onboarding_service: The onboarding pipeline, when one is wired.
        identity: The newly created agent identity.
    """
    if onboarding_service is None:
        return
    try:
        await onboarding_service.start_onboarding(str(identity.id))
    except DomainError as exc:
        # Broader than OnboardingError because the whole pipeline behind it
        # can fail (a persistence error, say), and this runs AFTER the agent
        # is registered and flipped INSTANTIATED: anything escaping here
        # reports a hire that fully succeeded as a failure, and the approvals
        # controller then surfaces an error for an agent that already exists.
        # System errors still propagate.
        logger.warning(
            HR_HIRING_INSTANTIATED,
            agent_id=str(identity.id),
            warning="onboarding_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = ["register_agent", "resolve_new_hire_model", "try_onboard"]
