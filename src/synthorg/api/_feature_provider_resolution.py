"""Model-aware provider resolution for boot-wired LLM features.

Shared by every feature builder that resolves a per-feature model
setting (LLM judge, chief-of-staff chat/propose/narrator, charter
interview, eval loop) to a concrete provider driver. Picking "the
first registered provider" is wrong once more than one provider is
registered: the per-feature model setting is provider-agnostic, so a
naive first pick can route the call to a driver that does not serve
the configured model, surfacing as a request-time model-not-found
error instead of a clear wiring-time failure.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

# TYPE_CHECKING-only: PEP 649 defers annotation evaluation, so these
# names resolve lazily and never force a runtime import (the registry
# / driver modules are heavy and would risk a cold-import cycle here).
if TYPE_CHECKING:
    from synthorg.providers.base import BaseCompletionProvider
    from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


def resolve_feature_provider(
    provider_registry: ProviderRegistry,
    model: str | None,
    *,
    feature: str,
) -> BaseCompletionProvider | None:
    """Resolve the provider serving *model* for a boot-wired feature.

    Returns:
        The serving driver, or ``None`` -- the feature stays unwired --
        when the model is not configured, the registry is empty, or no
        provider serves the model. Each case logs an actionable note
        naming the model and the registered providers.
    """
    from synthorg.providers.errors import (  # noqa: PLC0415
        DriverNotRegisteredError,
        ModelNotFoundError,
    )
    from synthorg.settings.model_ref import parse_model_ref  # noqa: PLC0415

    if not model:
        logger.info(
            API_APP_STARTUP,
            note="feature model not configured; feature stays unwired",
            feature=feature,
        )
        return None

    # A model-assignment setting stores a ``ModelRef``: an explicit provider
    # binds the model to *that* driver's catalogue rather than the first that
    # happens to serve the model id. A legacy bare string parses to a
    # provider-less ref and keeps the historic resolve-by-model behaviour.
    ref = parse_model_ref(model)
    if not ref.model_id:
        logger.info(
            API_APP_STARTUP,
            note="feature model not configured; feature stays unwired",
            feature=feature,
        )
        return None

    if ref.provider:
        try:
            driver = provider_registry.get(ref.provider)
        except DriverNotRegisteredError as exc:
            logger.warning(
                API_APP_STARTUP,
                note="feature provider (from model ref) not registered;"
                " feature stays unwired",
                feature=feature,
                provider=ref.provider,
                model=ref.model_id,
                available=list(provider_registry.list_providers()),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        logger.info(
            API_APP_STARTUP,
            note="feature provider resolved",
            feature=feature,
            provider=ref.provider,
            model=ref.model_id,
        )
        return driver

    try:
        name, driver = provider_registry.resolve_for_model(ref.model_id)
    except (DriverNotRegisteredError, ModelNotFoundError) as exc:
        logger.warning(
            API_APP_STARTUP,
            note="feature provider resolution failed; feature stays unwired",
            feature=feature,
            model=ref.model_id,
            available=list(provider_registry.list_providers()),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    logger.info(
        API_APP_STARTUP,
        note="feature provider resolved",
        feature=feature,
        provider=name,
        model=ref.model_id,
    )
    return driver
