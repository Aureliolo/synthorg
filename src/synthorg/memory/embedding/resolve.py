"""Embedder config resolution with priority chain.

Resolves an :class:`EmbedderConfig` from the priority chain:

1. Settings override (runtime-editable via dashboard)
2. YAML config override (``CompanyMemoryConfig.embedder``)
3. Auto-selection from available models using LMEB rankings

Callers use ``resolve_embedder_config()`` instead of constructing an
:class:`EmbedderConfig` manually. Resolution failing is deliberately an
error rather than a silent default: memory that cannot embed cannot
retrieve by meaning, and degrading quietly to keyword matching is how a
dead memory layer stays unnoticed.
"""

from synthorg.core.vector_limits import STORAGE_MAX_DIMENSIONS
from synthorg.memory.config import (
    CompanyMemoryConfig,
    EmbedderOverrideConfig,
)
from synthorg.memory.embedding.config import EmbedderConfig
from synthorg.memory.embedding.rankings import DeploymentTier
from synthorg.memory.embedding.selector import (
    infer_deployment_tier,
    select_embedding_model,
)
from synthorg.memory.errors import MemoryConfigError
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_EMBEDDER_AUTO_SELECT_FAILED,
    MEMORY_EMBEDDER_AUTO_SELECTED,
    MEMORY_EMBEDDER_PROVIDER_INFERRED,
)

logger = get_logger(__name__)


def _merge_override(
    override: EmbedderOverrideConfig | None,
    *,
    fallback_provider: str | None,
    fallback_model: str | None,
    fallback_dims: int | None,
) -> tuple[str | None, str | None, int | None]:
    """Merge an override with fallback values.

    Override fields take precedence; ``None`` falls through to
    fallback.

    Returns:
        Tuple ``(str | None, str | None, int | None)``.
    """
    if override is None:
        return fallback_provider, fallback_model, fallback_dims
    return (
        override.provider if override.provider is not None else fallback_provider,
        override.model if override.model is not None else fallback_model,
        override.dims if override.dims is not None else fallback_dims,
    )


def _dims_overridden(*overrides: EmbedderOverrideConfig | None) -> bool:
    """Whether any override in the chain set the vector width itself.

    Returns:
        ``True`` when an operator pinned ``dims`` rather than inheriting the
        auto-selected model's catalogued width.
    """
    return any(o is not None and o.dims is not None for o in overrides)


def _auto_select_from_lmeb(
    available_models: tuple[str, ...],
    tier: DeploymentTier,
) -> tuple[str | None, int | None]:
    """Auto-select model and dims from LMEB rankings.

    Tries tier-filtered first, then falls back to all tiers.

    Returns:
        ``(model_id, dims)`` or ``(None, None)`` if no match.
    """
    # The selector falls back to all tiers internally when the inferred tier
    # has no ranked match, so a single call suffices.
    selection = select_embedding_model(
        available_models,
        deployment_tier=tier,
    )
    if selection is not None:
        logger.info(
            MEMORY_EMBEDDER_AUTO_SELECTED,
            model_id=selection.model_id,
            tier=tier.value,
            ranking_source=selection.source,
            ranking_model=selection.ranking_model_id,
            dims=selection.output_dims,
        )
        return selection.model_id, selection.output_dims
    return None, None


def resolve_embedder_config(
    memory_config: CompanyMemoryConfig,
    available_models: tuple[str, ...] = (),
    *,
    provider_preset_name: str | None = None,
    has_gpu: bool | None = None,
    settings_override: EmbedderOverrideConfig | None = None,
) -> EmbedderConfig:
    """Resolve the effective embedder configuration.

    Priority chain (highest first):

    1. ``settings_override`` (runtime settings from dashboard)
    2. ``memory_config.embedder`` (YAML config override)
    3. Auto-selection from ``available_models`` using LMEB rankings

    Args:
        memory_config: Company-wide memory configuration.
        available_models: Model identifiers discovered from the
            connected provider(s).
        provider_preset_name: Provider preset name for tier inference.
        has_gpu: Whether the host has a GPU (for tier inference).
        settings_override: Runtime settings override (highest priority).

    Returns:
        A fully-populated ``EmbedderConfig``.

    Raises:
        MemoryConfigError: If no embedding model can be resolved
            (no overrides and no LMEB match in available models).
    """
    tier = infer_deployment_tier(provider_preset_name, has_gpu=has_gpu)
    auto_model, auto_dims = _auto_select_from_lmeb(available_models, tier)

    # Apply YAML config override (second priority).
    provider, model, dims = _merge_override(
        memory_config.embedder,
        fallback_provider=None,
        fallback_model=auto_model,
        fallback_dims=auto_dims,
    )

    # Apply settings override (highest priority).
    provider, model, dims = _merge_override(
        settings_override,
        fallback_provider=provider,
        fallback_model=model,
        fallback_dims=dims,
    )
    dims_explicit = _dims_overridden(memory_config.embedder, settings_override)

    model, dims = _resolved_or_refused(
        model, dims, available_models=available_models, tier=tier
    )
    if provider is None:
        provider = _inferred_provider(model)

    return EmbedderConfig(
        provider=provider,
        model=model,
        dims=dims,
        dims_explicit=dims_explicit,
    )


def _resolved_or_refused(
    model: str | None,
    dims: int | None,
    *,
    available_models: tuple[str, ...],
    tier: DeploymentTier,
) -> tuple[str, int]:
    """Refuse a width or model the store could never serve.

    Returns:
        The model and width, both known present and within the ceiling.
        Returned rather than merely checked so the guarantee survives the
        hand-off to ``EmbedderConfig``, which takes neither as optional.

    Raises:
        MemoryConfigError: If nothing resolved a model and width, or if
            the resolved width exceeds the vector store's ceiling.
    """
    if model is None or dims is None:
        logger.warning(
            MEMORY_EMBEDDER_AUTO_SELECT_FAILED,
            available_models=len(available_models),
            tier=tier.value,
            reason="no LMEB-ranked model available and no override",
        )
        msg = (
            "Could not resolve embedding model configuration: "
            "no LMEB-ranked model found in available models "
            "and no manual override provided"
        )
        raise MemoryConfigError(msg)

    if dims > STORAGE_MAX_DIMENSIONS:
        # The settings registry caps an operator override, but an
        # auto-selected width comes from the model ranking and never passes
        # through it, so without this a wide ranked model reaches the store
        # and fails inside ALTER TABLE as an opaque driver error.
        logger.warning(
            MEMORY_EMBEDDER_AUTO_SELECT_FAILED,
            model=model,
            dims=dims,
            storage_ceiling=STORAGE_MAX_DIMENSIONS,
            reason="resolved width exceeds the vector store's storage ceiling",
        )
        msg = (
            f"Embedding width {dims} exceeds the vector store's ceiling of "
            f"{STORAGE_MAX_DIMENSIONS}; choose a narrower embedding model "
            f"or set memory.embedder_dims to truncate it."
        )
        raise MemoryConfigError(msg)
    return model, dims


def _inferred_provider(model: str) -> str:
    """Return the provider to assume for a model that named none.

    An LMEB-auto-selected open model is served by name (the local /
    self-hosted convention litellm dispatches directly), so the provider
    mirrors the model. Logged rather than assumed silently: for a hosted
    provider that same-name assumption is wrong, and an operator whose
    model fails to embed needs the boot log to say "the provider was
    guessed" rather than debugging an opaque auth error.

    Returns:
        The model name, doubling as the provider.
    """
    logger.info(
        MEMORY_EMBEDDER_PROVIDER_INFERRED,
        model=model,
        reason="auto-selected model has no explicit provider; using model name",
    )
    return model
