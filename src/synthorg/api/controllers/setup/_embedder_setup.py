# module-kind: code
"""Template-driven agent creation and embedder binding.

Expands template agents, matches a model to each one, persists the agent
array, collects provider model IDs, and measures the width of the
embedding model the operator chose. The agents-settings write reuses the
shared ``AGENT_LOCK`` so it serialises against the setup controllers'
read-modify-write paths.

Nothing here chooses an embedding model. Setup binds the operator's
choice or reports that none was made.
"""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass

from synthorg.api.controllers.setup._runtime_wiring import AGENT_LOCK
from synthorg.api.controllers.setup.company_helpers import read_name_locales
from synthorg.api.controllers.setup_agents import (
    agents_to_summaries,
    expand_template_agents,
)
from synthorg.api.controllers.setup_model_assignment import match_and_assign_models
from synthorg.api.controllers.setup_models import SetupAgentSummary
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ProviderModelCoverageInsufficientError
from synthorg.memory.embedding.probe import is_builtin_embedder, probe_embedder_dims
from synthorg.memory.embedding.resolve import DimsProbe, EndpointResolver
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_EMBEDDER_PROBE_FAILED,
    MEMORY_EMBEDDER_UNRESOLVED,
)
from synthorg.observability.events.setup import (
    SETUP_MODEL_ID_COLLECTION_ERROR,
    SETUP_PROVIDER_MODEL_COVERAGE_INSUFFICIENT,
    SETUP_STATUS_SETTINGS_UNAVAILABLE,
)
from synthorg.persistence.state import persistence_of
from synthorg.providers.embedding_endpoint import EmbeddingEndpoint
from synthorg.providers.state import provider_management_of
from synthorg.settings.model_ref import parse_model_ref
from synthorg.settings.service_protocol import SettingsServiceProtocol
from synthorg.settings.state import SettingsStateSlice, config_resolver_of
from synthorg.templates.loader import LoadedTemplate
from synthorg.templates.model_matcher_config import ModelMatcherConfig

logger = get_logger(__name__)

# Inverted-convention result from ``bind_chosen_embedder``: ``None`` means
# the operator's chosen model answered a probe; a ``str`` carries the
# human-readable failure reason. Aliased here so the call site can pass the
# result directly to ``SetupCompleteResponse.embedder_failure_reason``
# without re-stating the inversion at every call.
type EmbedderSelectResult = str | None


def _validate_model_coverage(providers: Mapping[str, object]) -> None:
    """Reject provider sets that cannot satisfy capability classification.

    The model matcher tolerates fewer than three models per provider by
    returning all models for every rung in that case, so this gate only
    blocks the truly empty case: zero models across all registered
    providers. Setups with a couple of models continue to work; the
    matcher just assigns the same model to every rung.

    Args:
        providers: Provider name -> config mapping resolved from
            ``provider_management.list_providers()``.

    Raises:
        ProviderModelCoverageInsufficientError: When NO models are
            available across the registered providers. The frontend
            reads ``error_detail.error_code`` (2004) to surface a
            "Go back to Providers step" affordance instead of a
            generic Retry button.
    """
    total_models = sum(len(getattr(cfg, "models", ())) for cfg in providers.values())
    if total_models > 0:
        return
    msg = (
        "No configured provider exposes any models. Go back to the "
        "Providers step, add at least one model to a provider, then "
        "return here to apply the template."
    )
    logger.warning(
        SETUP_PROVIDER_MODEL_COVERAGE_INSUFFICIENT,
        provider_count=len(providers),
        total_model_count=0,
    )
    raise ProviderModelCoverageInsufficientError(msg)


async def _resolve_matcher_config(
    app_state: AppState,
) -> ModelMatcherConfig | None:
    """Resolve matcher config; degrade to None on resolution failure.

    Non-critical bridge-config resolution failures (missing setting, validation
    error, persistence flake) AND projection failures (``from_bridge_config``
    raising on a tampered field) must both keep the template bootstrap alive;
    interpreter-critical errors propagate via ``reraise_critical``. Mirrors the
    fail-open pattern used by ``post_setup_reinit``.

    Returns:
        The ``ModelMatcherConfig`` value when present, ``None`` otherwise.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return None
    try:
        bridge_cfg = await config_resolver_of(app_state).get_engine_bridge_config()
        return ModelMatcherConfig.from_bridge_config(bridge_cfg)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            context="matcher_config",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def auto_create_template_agents(
    loaded: LoadedTemplate,
    app_state: AppState,
    settings_svc: SettingsServiceProtocol,
    *,
    variables: Mapping[str, object] | None = None,
    model_spend_profile: str = "balanced",
) -> tuple[SetupAgentSummary, ...]:
    """Render template agents, match models, persist, and return summaries.

    Renders via the shared renderer pipeline (resolving inheritance and
    head-roles), so the wizard's roster matches the engine's exactly.

    Returns:
        Tuple of the declared element types.
    """
    from synthorg.templates.preset_service import (  # noqa: PLC0415
        fetch_custom_presets_map,
    )

    async with asyncio.TaskGroup() as tg:
        loc_task = tg.create_task(read_name_locales(settings_svc))
        preset_task = tg.create_task(
            fetch_custom_presets_map(persistence_of(app_state).custom_presets),
        )
        prov_task = tg.create_task(
            provider_management_of(app_state).list_providers(),
        )
        matcher_task = tg.create_task(_resolve_matcher_config(app_state))
    agents = expand_template_agents(
        loaded,
        locales=loc_task.result(),
        custom_presets=preset_task.result(),
        variables=variables,
    )
    providers = prov_task.result()
    _validate_model_coverage(providers)
    agents = match_and_assign_models(
        agents,
        providers,
        matcher_task.result(),
        model_spend_profile=model_spend_profile,
    )

    async with AGENT_LOCK:
        await settings_svc.set("company", "agents", json.dumps(agents))

    return agents_to_summaries(agents)


async def collect_provider_models(
    app_state: AppState,
) -> tuple[tuple[str, str], ...]:
    """Extract ``(provider, model id)`` pairs from every configured provider.

    The provider-bound source both catalogue reads share. A MODEL_REF picker
    needs the pair rather than the bare id, because the same model id can be
    served by more than one provider and a provider-less assignment is
    rejected at write time.

    Best-effort: returns an empty tuple if the config resolver is not
    available or provider configs cannot be read for a non-critical
    reason; interpreter-critical errors propagate via ``reraise_critical``.

    Returns:
        Tuple of ``(provider_name, model_id)`` pairs.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return ()
    try:
        configs = await config_resolver_of(app_state).get_provider_configs()
        return tuple(
            (name, str(model.id)) for name, pc in configs.items() for model in pc.models
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETUP_MODEL_ID_COLLECTION_ERROR,
            check="collect_provider_models",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()


async def bind_chosen_embedder(
    *,
    settings_svc: SettingsServiceProtocol,
    measure_dims: DimsProbe = probe_embedder_dims,
    resolve_endpoint: EndpointResolver | None = None,
) -> EmbedderSelectResult:
    """Prove the embedder the operator chose can actually embed.

    Nothing is selected here. An operator who chose no model gets memory
    off and a reason saying so, never a model picked on their behalf and
    never the built-in embedder standing in for one.

    Called during setup completion, after providers are validated, so the
    probe reaches a provider that is known to work, and a binding that
    cannot embed is reported while the operator is still in setup rather
    than at the first memory write.

    The measured width is deliberately not persisted.
    ``memory.embedder_dims`` is the operator's own truncation pin, and
    writing a measurement into it makes the two indistinguishable: a width
    measured for one model then outlives it, and the next model's vectors
    are silently truncated to it as though that had been asked for. The
    width is measured again at boot, against whatever model is bound then.

    Args:
        settings_svc: Settings service for reading the operator's choice.
        measure_dims: Probe used to exercise the chosen model.
        resolve_endpoint: Looks up where the chosen provider is reachable,
            so the probe proves the operator's own endpoint answers rather
            than whichever host litellm defaults to.

    Returns:
        ``None`` on success, or a short human-readable reason string when
        no model was chosen, the pair was incomplete, or the model could
        not be probed. The inverted convention (None = success, str =
        failure) keeps the caller free to pass the result straight to
        ``SetupCompleteResponse.embedder_failure_reason``.
    """
    chosen = await _chosen_embedder(settings_svc)
    if chosen.failure_reason is not None:
        return chosen.failure_reason
    provider, model = chosen.provider, chosen.model

    if await _setting_text(settings_svc, "embedder_dims"):
        # An operator who pinned a width is asking for that width, usually
        # to bring a wide model under the store's index ceiling. Probing
        # would neither change nor validate that request.
        return None

    located = await locate_embedding_endpoint(
        provider, model, resolve_endpoint=resolve_endpoint
    )
    if located.failure_reason is not None:
        return located.failure_reason

    try:
        await measure_dims(provider=provider, model=model, endpoint=located.endpoint)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        reason = f"embedding model {model!r} did not answer a width probe"
        # The probe logs the call-level fault; this records the setup-level
        # outcome, which is what an operator reading the completion warning
        # will search for.
        logger.warning(
            MEMORY_EMBEDDER_PROBE_FAILED,
            stage="setup_completion",
            provider=provider,
            model=model,
            reason=reason,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return reason
    return None


@dataclass(frozen=True)
class ChosenEmbedder:
    """The operator's embedder pair, or why there is not one.

    Attributes:
        provider: Provider the chosen model is bound to.
        model: The chosen model id.
        failure_reason: Operator-facing reason no usable pair was read,
            or ``None`` when one was.
    """

    provider: str = ""
    model: str = ""
    failure_reason: str | None = None


@dataclass(frozen=True)
class LocatedEndpoint:
    """Where a width probe should be addressed, or why it cannot be.

    Attributes:
        endpoint: Where the provider answers. ``None`` is a success too:
            the built-in embedder needs no lookup, and a caller that
            supplied no resolver is letting the driver route itself.
        failure_reason: Operator-facing reason the lookup failed, or
            ``None`` when it did not.
    """

    endpoint: EmbeddingEndpoint | None = None
    failure_reason: str | None = None


async def _chosen_embedder(
    settings_svc: SettingsServiceProtocol,
) -> ChosenEmbedder:
    """Read the operator's chosen embedder pair.

    Returns:
        The pair, or the reason there is no usable one. An unbound model
        is a distinct failure from no model at all: the first is a
        half-finished choice, the second is no choice.
    """
    ref = parse_model_ref(await _setting_text(settings_svc, "embedder_model"))
    provider, model = ref.provider, ref.model_id
    if not model:
        logger.warning(
            MEMORY_EMBEDDER_UNRESOLVED,
            stage="setup_completion",
            reason="no_model_chosen",
            remedy="set memory.embedder_model to a provider-bound reference",
        )
        return ChosenEmbedder(
            failure_reason=("no embedding model chosen; agents will run without recall")
        )
    if not provider:
        logger.warning(
            MEMORY_EMBEDDER_UNRESOLVED,
            stage="setup_completion",
            reason="model_missing_provider",
            model=model,
            remedy="set memory.embedder_model to a provider-bound reference",
        )
        return ChosenEmbedder(
            failure_reason=(
                f"embedding model {model!r} has no provider bound to it; "
                f"set memory.embedder_model to a provider-bound reference"
            )
        )
    return ChosenEmbedder(provider=provider, model=model)


async def locate_embedding_endpoint(
    provider: str,
    model: str,
    *,
    resolve_endpoint: EndpointResolver | None,
) -> LocatedEndpoint:
    """Look up where *provider* answers, mapping a failure to its reason.

    Shared by every path that has to address an embedding model at the
    operator's own endpoint rather than at whichever host the driver
    would default to, so the built-in bypass and the failure wording
    cannot drift between them.

    Args:
        provider: Provider the model is bound to.
        model: The model id, for the failure message.
        resolve_endpoint: The lookup; ``None`` leaves routing to the
            driver, which is correct only for a hosted provider.

    Returns:
        Where the provider answers, or the reason it could not be found.
    """
    # Resolved before any probe, and skipped for the built-in: an argument
    # expression is evaluated before the callee runs, so inlining this would
    # look up a provider named "builtin" that cannot exist and fail the one
    # embedder whose width needs no lookup at all.
    if resolve_endpoint is None or is_builtin_embedder(provider, model):
        return LocatedEndpoint()
    try:
        return LocatedEndpoint(endpoint=await resolve_endpoint(provider))
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        # Distinct from a probe failure: the provider could not be located
        # or authenticated, which is a binding the operator fixes in
        # configuration, not a model that failed to answer.
        reason = (
            f"embedding provider {provider!r} could not be resolved, so "
            f"{model!r} has no endpoint to answer from"
        )
        logger.warning(
            MEMORY_EMBEDDER_UNRESOLVED,
            stage="setup_completion",
            provider=provider,
            model=model,
            reason=reason,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return LocatedEndpoint(failure_reason=reason)


async def _setting_text(
    settings_svc: SettingsServiceProtocol,
    key: str,
) -> str:
    """Read one memory setting as trimmed text.

    Returns:
        The trimmed value, or an empty string when unset or non-textual.
    """
    value = (await settings_svc.get("memory", key)).value
    return value.strip() if isinstance(value, str) else ""
