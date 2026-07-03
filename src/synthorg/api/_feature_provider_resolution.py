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
    model: object,
    *,
    feature: str,
) -> BaseCompletionProvider | None:
    """Resolve the provider serving *model* for a boot-wired feature.

    Returns:
        The serving driver, or ``None`` -- the feature stays unwired --
        when the registry is empty or no provider serves the model.
        Both cases log an actionable WARNING naming the model and the
        registered providers.
    """
    from synthorg.providers.errors import (  # noqa: PLC0415
        DriverNotRegisteredError,
        ModelNotFoundError,
    )

    try:
        name, driver = provider_registry.resolve_for_model(str(model))
    except (DriverNotRegisteredError, ModelNotFoundError) as exc:
        logger.warning(
            API_APP_STARTUP,
            note="feature provider resolution failed; feature stays unwired",
            feature=feature,
            model=str(model),
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
        model=str(model),
    )
    return driver
