# module-kind: code
"""Shared MODEL_REF provider resolution for boot / runtime wiring.

A model-assignment setting stores a :class:`~synthorg.settings.model_ref.ModelRef`
(``{provider, model_id}``). This resolves the ref's *explicit* provider against
the live registry. A model assignment is always an explicit ``(provider, model)``
pair: a ref with no provider (or an unregistered one) resolves to ``None`` so the
caller fails loud rather than silently binding to whichever provider happens to
be the boot default.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.state import provider_registry_of
from synthorg.settings.model_ref import ModelRef

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


def resolve_ref_provider(
    app_state: AppState,
    ref: ModelRef,
    *,
    event: str,
    subject: str,
) -> CompletionProvider | None:
    """Resolve a ``ModelRef``'s explicit provider against the registry.

    A model assignment must name its provider: an empty or unregistered
    provider is never auto-resolved to a default, because with two gateways
    advertising an overlapping id that pick is ambiguous. Both cases log
    under *event* naming *subject* (the feature) and return ``None`` so the
    caller fails loud or leaves the feature unwired.

    Returns:
        The driver for the ref's explicit provider, or ``None`` when the ref
        names no provider or an unregistered one.
    """
    if not ref.provider.strip():
        logger.warning(
            event,
            note=f"{subject} model ref has no provider; a (provider, model) pair"
            " is required",
        )
        return None
    try:
        return provider_registry_of(app_state).get(ref.provider)
    except DriverNotRegisteredError:
        logger.warning(
            event,
            note=f"{subject} model's selected provider is not registered",
            provider=ref.provider,
        )
        return None
