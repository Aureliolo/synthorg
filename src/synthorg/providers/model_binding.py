# module-kind: code
"""Shared MODEL_REF provider resolution for boot / runtime wiring.

A model-assignment setting stores a :class:`~synthorg.settings.model_ref.ModelRef`
(``{provider, model_id}``). This resolves the ref's provider against the live
registry, honouring an explicit provider and falling back to a caller-supplied
active provider when the ref names none or an unregistered one -- so a model
binds to the provider it was selected on, not the first that happens to serve
the id.
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
    active: CompletionProvider | None,
    event: str,
    subject: str,
) -> CompletionProvider | None:
    """Resolve a ``ModelRef`` provider, falling back to *active*.

    An explicit ``ref.provider`` binds to that registered driver; an empty
    provider, or one that is not registered, falls back to *active* (the
    first-registered / boot provider), logging the fallback under *event*
    and naming *subject* (the feature).

    Returns:
        The provider the model resolves against, or *active* (which may be
        ``None`` when the caller has no active provider).
    """
    if not ref.provider.strip():
        return active
    try:
        return provider_registry_of(app_state).get(ref.provider)
    except DriverNotRegisteredError:
        logger.warning(
            event,
            note=f"{subject} model's selected provider is not registered;"
            " falling back to the active provider",
            provider=ref.provider,
        )
        return active
