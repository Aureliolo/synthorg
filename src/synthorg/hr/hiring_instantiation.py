# module-kind: code
"""Turning an approved hire into a live agent.

The three steps between "a human said yes" and "the agent is on the roster":
resolve the pair it will run on, register it, and start its onboarding. They
are free functions taking the collaborator each needs, so the hiring service
keeps the decision flow and the durable request state, and this module keeps
the part that touches the roster.
"""

from synthorg.config.model_metadata import is_tool_capable
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.domain_errors import DomainError
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import (
    AgentAlreadyRegisteredError,
    HiringError,
)
from synthorg.hr.hire_model_proposal import ProviderCatalogue
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
from synthorg.settings.model_ref import parse_model_ref

logger = get_logger(__name__)


async def resolve_hire_model(
    request: HiringRequest,
    *,
    catalogue: ProviderCatalogue | None,
) -> ModelConfig:
    """Read the pair THIS hire was approved on, refusing one that is not real.

    The pair travels with the request rather than being read from a standing
    org-wide setting, because it is part of what the operator approved: the
    approval proposes pairs from the models they actually have and records
    the one they picked. A setting could only ever give every hire the same
    answer, and gave every hire NO answer whenever it was unset, which is how
    an approval came to be raised for a hire the system would then refuse.

    There is deliberately nothing to fall back to: an agent registered
    against a placeholder provider joins the roster looking staffed and fails
    every dispatch it is ever given.

    A pair recorded at proposal time and a pair that still exists are
    different claims, and only the second is the one that matters here.
    Approval is a human step, so an arbitrary interval separates the two, and
    the catalogue is live: the operator can delete the connection, drop the
    model, or have runtime tool-call failures downgrade it out of eligibility.
    Every one of those produces the same roster entry as the placeholder this
    already refuses, so this asks the catalogue the same question the
    proposal asked and refuses the same way.

    With no catalogue there is nothing to ask, and that is a refusal too
    rather than a pass: a provider is a registered CONNECTION, so a pair
    nothing can confirm is one is exactly the unverified binding above. The
    reasoning that it cannot happen (a pipeline with no catalogue proposes
    nothing, so a bound request should be unreachable) is true of the
    proposal path alone, and ``bind_model`` takes any syntactically valid
    ``MODEL_REF`` from a caller of its own. Nothing legitimate is lost:
    wiring declines the whole hiring subsystem without a catalogue.

    Args:
        request: The approved request, carrying the pair it was approved on.
        catalogue: The operator's configured providers, read live, or ``None``
            when the pipeline was built without one.

    Raises:
        HiringError: When the request carries no pair, which means nothing
            was proposable when the approval was raised, or when the pair it
            carries is not one the operator's live catalogue offers.

    Returns:
        The pair the new agent runs on.
    """
    ref = parse_model_ref(request.bound_model_ref or "")
    if not ref.is_bound:
        msg = (
            f"Hiring request {request.id!s} carries no model binding, so there "
            "is nothing to register this agent against. No configured model "
            "was proposable when the approval was raised."
        )
        logger.warning(
            HR_HIRING_MODEL_UNSET,
            request_id=str(request.id),
            role=str(request.role),
            error=msg,
        )
        raise HiringError(msg)
    if catalogue is None:
        msg = (
            f"Hiring request {request.id!s} names {ref.provider}/{ref.model_id}, "
            "but no provider catalogue is wired, so nothing can confirm that is "
            "a connection this organisation has. Refusing rather than "
            "registering an agent whose every dispatch would fail."
        )
        logger.warning(
            HR_HIRING_MODEL_UNSET,
            request_id=str(request.id),
            role=str(request.role),
            error=msg,
        )
        raise HiringError(msg)
    await _require_still_offerable(request, ref.provider, ref.model_id, catalogue)
    return ModelConfig(
        provider=NotBlankStr(ref.provider),
        model_id=NotBlankStr(ref.model_id),
    )


async def _require_still_offerable(
    request: HiringRequest,
    provider: str,
    model_id: str,
    catalogue: ProviderCatalogue,
) -> None:
    """Refuse a binding the operator's live catalogue no longer offers.

    Args:
        request: The request being instantiated, for the message and the log.
        provider: The connection half of the recorded pair.
        model_id: The model half of the recorded pair.
        catalogue: The operator's configured providers, read live.

    Raises:
        HiringError: When the connection is gone, the model is gone from it,
            or the model can no longer call a tool.
    """
    providers = await catalogue.list_providers()
    config = providers.get(provider)
    if config is None:
        reason = f"connection {provider!r} is no longer configured"
    else:
        model = next((m for m in config.models if str(m.id) == model_id), None)
        if model is None:
            reason = f"connection {provider!r} no longer carries model {model_id!r}"
        elif not is_tool_capable(model.metadata):
            reason = (
                f"model {model_id!r} on {provider!r} can no longer call a tool, "
                "which every agent needs"
            )
        else:
            return
    msg = (
        f"Hiring request {request.id!s} was approved on {provider}/{model_id}, "
        f"but {reason}. Re-approve it on a pair you still have rather than "
        "registering an agent that cannot run."
    )
    logger.warning(
        HR_HIRING_MODEL_UNSET,
        request_id=str(request.id),
        role=str(request.role),
        error=msg,
    )
    raise HiringError(msg)


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


__all__ = ["register_agent", "resolve_hire_model", "try_onboard"]
