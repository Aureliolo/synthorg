# module-kind: orchestrator
"""Boot resolution of the design image provider from settings.

Kept out of ``_engine_assembly`` (size budget) but part of the same boot
assembly: reads the ``design`` namespace settings, resolves the selected
image model to its serving provider, and wraps it in a
:class:`ProviderImageProvider` so the ``image_generator`` tool routes image
generation through the normal provider + model-management layer.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.design import DESIGN_IMAGE_PROVIDER_BOUND
from synthorg.providers.image_generation import ImageGenerationProvider
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.tools.design.image_generator import ImageProvider

logger = get_logger(__name__)

_DESIGN_NS: str = "design"


async def build_image_provider_or_none(
    app_state: AppState,
) -> ImageProvider | None:
    """Resolve the boot image provider from the ``design`` settings, or ``None``.

    Returns ``None`` (so the ``image_generator`` tool is not registered) when
    image generation is disabled, no image model is selected, the selected
    model is not served by any connected provider, or it is not image-capable.
    Mirrors the external-access wiring: fail-open so a misconfigured image
    feature never crashes the agent runtime, at distinct log levels so a
    transient resolve failure is distinguishable from a misconfiguration.

    Returns:
        A :class:`ProviderImageProvider` bound to the selected image model, or
        ``None`` when image generation is off or unbuildable.
    """
    logger.debug(API_APP_STARTUP, service="design_image", context="resolve_start")
    resolver = config_resolver_of(app_state)
    try:
        enabled = await resolver.get_bool(_DESIGN_NS, "image_generation_enabled")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        # Transient settings-resolve failure (distinct from a misconfig):
        # WARNING, not ERROR.
        logger.warning(
            API_APP_STARTUP,
            service="design_image",
            context="enabled_flag_resolve",
            note="could not resolve design.image_generation_enabled; feature off",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if not enabled:
        return None

    registry = app_state.slice(ProvidersStateSlice).registry
    if registry is None:
        # Feature explicitly enabled but unbuildable: an operator misconfig
        # that silently disables a paid capability, so ERROR not WARNING.
        logger.error(
            API_APP_STARTUP,
            service="design_image",
            note="image generation enabled but no provider registry is wired",
        )
        return None
    try:
        model_id_raw = await resolver.get_str(_DESIGN_NS, "image_model")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        # Transient settings-resolve failure (distinct from a misconfig):
        # WARNING, not ERROR. Fail open so worker startup is never broken.
        logger.warning(
            API_APP_STARTUP,
            service="design_image",
            context="image_model_resolve",
            note="could not resolve design.image_model; feature off",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    model_id = model_id_raw.strip()
    if not model_id:
        logger.error(
            API_APP_STARTUP,
            service="design_image",
            note="image generation enabled but no design.image_model is selected",
        )
        return None

    provider = await _resolve_serving_provider(registry, model_id)
    if provider is None:
        return None

    from synthorg.tools.design.provider_image_provider import (  # noqa: PLC0415
        ProviderImageProvider,
    )

    logger.info(
        DESIGN_IMAGE_PROVIDER_BOUND,
        service="design_image",
        note="wired",
        model=model_id,
    )
    return ProviderImageProvider(provider=provider, model=model_id)


async def _resolve_serving_provider(
    registry: ProviderRegistry,
    model_id: str,
) -> ImageGenerationProvider | None:
    """Resolve ``model_id`` to an image-capable serving provider, or ``None``.

    Returns ``None`` (logging the reason) when the model is not served by a
    connected provider, is not image-capable, or the serving driver cannot
    structurally generate images. Every miss after the feature was enabled
    is a misconfiguration, so it logs at ERROR.

    Returns:
        The image-capable serving provider, or ``None`` when unbuildable.
    """
    try:
        _, provider = registry.resolve_for_model(model_id)
        capabilities = await provider.get_model_capabilities(model_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            service="design_image",
            context="image_model_resolve",
            note="design.image_model not served by a connected provider; tool off",
        )
        return None
    if not capabilities.supports_image_generation:
        logger.error(
            API_APP_STARTUP,
            service="design_image",
            note="design.image_model is not an image-generation model; tool off",
            model=model_id,
        )
        return None
    if not isinstance(provider, ImageGenerationProvider):
        logger.error(
            API_APP_STARTUP,
            service="design_image",
            note="serving provider cannot generate images; tool off",
            model=model_id,
        )
        return None
    return provider
