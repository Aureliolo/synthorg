"""Provider/model validation for the first-run setup controller.

Verifies that agent model assignments reference a provider that still
exists and a model that provider still exposes. Kept separate from
``setup_agents`` so the template-expansion and persistence helpers stay
focused on building agent dicts rather than cross-checking them against
live provider config.
"""

from collections.abc import Mapping

from synthorg.api.controllers.setup_models import (
    SetupAgentRequest,
    UpdateAgentModelRequest,
)
from synthorg.config.schema import ProviderConfig
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.observability import get_logger
from synthorg.observability.events.setup import (
    SETUP_MODEL_NOT_FOUND,
    SETUP_PROVIDER_NOT_FOUND,
)

logger = get_logger(__name__)


def _validate_provider_model_pair(
    providers: Mapping[str, ProviderConfig],
    provider_name: str,
    model_id: str,
) -> None:
    """Validate that a provider exists and contains the given model.

    Args:
        providers: Provider name -> config mapping.
        provider_name: Provider to look up.
        model_id: Model identifier to find within the provider.

    Raises:
        NotFoundError: If the provider does not exist.
        ValidationError: If the model is not in the provider.
    """
    if provider_name not in providers:
        msg = f"Provider {provider_name!r} not found"
        logger.warning(SETUP_PROVIDER_NOT_FOUND, provider=provider_name)
        raise NotFoundError(msg)

    provider_config = providers[provider_name]
    known_ids = {m.id for m in provider_config.models}
    if model_id not in known_ids:
        msg = f"Model {model_id!r} not found in provider {provider_name!r}"
        logger.warning(
            SETUP_MODEL_NOT_FOUND,
            provider=provider_name,
            model=model_id,
        )
        raise ValidationError(msg)


def validate_model_assignment(
    providers: Mapping[str, ProviderConfig],
    data: UpdateAgentModelRequest,
) -> None:
    """Validate provider and model for a model reassignment request.

    Args:
        providers: Provider name -> config mapping.
        data: Model assignment payload.

    Raises:
        NotFoundError: If the provider does not exist.
        ValidationError: If the model is not in the provider.
    """
    _validate_provider_model_pair(providers, data.model_provider, data.model_id)


def validate_persisted_agents_against_providers(
    providers: Mapping[str, ProviderConfig],
    agents: list[dict[str, object]],
) -> None:
    """Verify every persisted agent points at a real provider+model pair.

    Called from the setup-complete flow so an agent whose provider /
    model was deleted between agent creation and setup completion
    cannot land on a "complete" dashboard with broken model references.

    Args:
        providers: Provider name -> config mapping resolved from
            provider_management.list_providers().
        agents: Persisted agent dicts loaded from the ``company.agents``
            setting (each entry has ``model.provider`` and
            ``model.model_id`` keys).

    Raises:
        ValidationError: If any agent references a provider that no
            longer exists OR a model the provider no longer exposes.
            The error message names the offending agent + reference so
            the wizard can highlight the right row.
    """
    for idx, agent in enumerate(agents):
        model = agent.get("model")
        if not isinstance(model, dict):
            continue
        provider_name = model.get("provider")
        model_id = model.get("model_id")
        if not isinstance(provider_name, str) or not isinstance(model_id, str):
            continue
        agent_label = agent.get("name") or f"agent {idx}"
        if provider_name not in providers:
            msg = (
                f"Agent {agent_label!r} references provider "
                f"{provider_name!r}, which is no longer configured. "
                "Re-edit the agent or restore the provider before "
                "completing setup."
            )
            logger.warning(
                SETUP_PROVIDER_NOT_FOUND,
                provider=provider_name,
                agent_index=idx,
            )
            raise ValidationError(msg)
        provider_config = providers[provider_name]
        known_ids = {m.id for m in provider_config.models}
        if model_id not in known_ids:
            msg = (
                f"Agent {agent_label!r} references model "
                f"{model_id!r} on provider {provider_name!r}, which "
                "the provider no longer exposes. Re-edit the agent's "
                "model before completing setup."
            )
            logger.warning(
                SETUP_MODEL_NOT_FOUND,
                provider=provider_name,
                model=model_id,
                agent_index=idx,
            )
            raise ValidationError(msg)


def validate_provider_and_model(
    providers: Mapping[str, ProviderConfig],
    data: SetupAgentRequest,
) -> None:
    """Validate that the provider and model exist.

    Args:
        providers: Provider name -> config mapping from management service.
        data: Agent creation payload.

    Raises:
        NotFoundError: If the provider does not exist.
        ValidationError: If the model is not in the provider.
    """
    _validate_provider_model_pair(providers, data.model_provider, data.model_id)
