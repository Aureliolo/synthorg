"""Model-aware provider resolution for boot-wired LLM features.

Shared by every feature builder that resolves a per-feature model
setting (LLM judge, chief-of-staff chat/propose/narrator, charter
interview, eval loop) to a concrete provider driver. A model assignment
is always an explicit ``(provider, model)`` pair: there is no
"resolve the model id against whichever provider happens to serve it"
path, because with two gateways advertising an overlapping id that pick
is ambiguous (and silently routed calls to the alphabetically-first
provider). A provider-less setting leaves the feature unwired rather
than auto-selecting a gateway.
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
    """Resolve the provider a feature's explicit model ref is bound to.

    Returns:
        The driver for the ref's explicit provider, or ``None`` -- the
        feature stays unwired -- when the model is not configured, the
        ref carries no provider (a bare model id is never auto-resolved),
        or the named provider is not registered. Each case logs an
        actionable note naming the model and the registered providers.
    """
    from synthorg.providers.errors import DriverNotRegisteredError  # noqa: PLC0415
    from synthorg.settings.model_ref import parse_model_ref  # noqa: PLC0415

    if not model:
        logger.info(
            API_APP_STARTUP,
            note="feature model not configured; feature stays unwired",
            feature=feature,
        )
        return None

    ref = parse_model_ref(model)
    if not ref.model_id:
        logger.info(
            API_APP_STARTUP,
            note="feature model not configured; feature stays unwired",
            feature=feature,
        )
        return None

    if not ref.provider:
        # A model assignment must name its provider explicitly; a bare id is
        # never resolved against "whichever provider serves it" because that
        # pick is ambiguous across gateways with an overlapping id.
        logger.warning(
            API_APP_STARTUP,
            note="feature model ref has no provider; a (provider, model) pair is"
            " required, feature stays unwired",
            feature=feature,
            model=ref.model_id,
        )
        return None

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
