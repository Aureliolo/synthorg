# module-kind: code
"""Shared MODEL_REF provider resolution for boot / runtime wiring.

A model-assignment setting stores a :class:`~synthorg.settings.model_ref.ModelRef`
(``{provider, model_id}``). This resolves the ref's *explicit* provider against
the live registry. A model assignment is always an explicit ``(provider, model)``
pair: a ref with no provider (or an unregistered one) resolves to ``None`` so the
caller fails loud rather than silently binding to whichever provider happens to
be the boot default.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.state import provider_registry_of
from synthorg.settings.model_ref import ModelRef

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BoundCompletion:
    """A resolved dispatch target: a connection's client plus a model id.

    Travels as one value because half of it names no dispatch target. A
    provider is a registered *connection*, carrying its own credentials,
    endpoint and quota, so the same model id reached through two of them is
    two different calls, billed and rate-limited separately. A consumer that
    took the two halves separately could be handed a client for one
    connection and a model id chosen for another.

    Attributes:
        provider: The client for the connection the operator's pair names.
        model: The model id to request on that connection.
    """

    provider: CompletionProvider
    model: NotBlankStr


async def resolve_bound_completion(
    app_state: AppState,
    *,
    namespace: str,
    key: str,
    unset_event: str,
    subject: str,
) -> BoundCompletion | None:
    """Resolve a ``MODEL_REF`` setting into a ready dispatch target.

    The one path from "an operator chose a pair" to "a call can be made":
    reads the assignment, refuses half a pair, and refuses a pair naming a
    connection that is not registered.

    Args:
        app_state: Application state carrying the resolver and registry.
        namespace: Settings namespace holding the model reference.
        key: The ``MODEL_REF`` setting key.
        unset_event: Event name to log an unresolved pair under.
        subject: The feature name, for the log line.

    Returns:
        The bound target, or ``None`` when the caller must leave the feature
        unwired rather than dispatch on a connection nobody chose.
    """
    from synthorg.settings.bound_model import resolve_bound_model  # noqa: PLC0415

    ref = await resolve_bound_model(
        app_state, namespace=namespace, key=key, unset_event=unset_event
    )
    if ref is None:
        return None
    provider = resolve_ref_provider(app_state, ref, event=unset_event, subject=subject)
    if provider is None:
        return None
    return BoundCompletion(provider=provider, model=NotBlankStr(ref.model_id))


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
