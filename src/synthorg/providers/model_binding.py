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
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.failover_dispatch import (
    FailoverCompletionProvider,
    FailoverPolicy,
    FailoverRecorder,
)
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.model_ref import ModelRef
from synthorg.settings.state import SettingsStateSlice

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
    return BoundCompletion(
        provider=_with_declared_failover(
            app_state, provider, declared=ref, feature=f"{namespace}.{key}"
        ),
        model=NotBlankStr(ref.model_id),
    )


def _with_declared_failover(
    app_state: AppState,
    client: CompletionProvider,
    *,
    declared: ModelRef,
    feature: str,
) -> CompletionProvider:
    """Put the operator's declared alternate behind *client*.

    Applied here, at the one path from "an operator chose a pair" to "a call
    can be made", so every ``MODEL_REF`` system feature is covered without
    any of them knowing the mechanism exists. An agent's own pair, the
    gateway's per-run pair and the embedder all resolve elsewhere and are
    therefore structurally out of reach, which is the scope ruling rather
    than a check somebody has to remember.

    The wrapper is unconditional because the declaration is read live: a
    route added mid-incident has to take effect on the next call, and a
    wrapper decided at wiring time could only answer with the routes that
    existed at boot. It costs one settings read per dispatch, and returns
    the bare client the moment no route names this pair.

    Returns:
        The wrapped client, or *client* itself when the registry needed to
        reach an alternate is not wired.
    """
    providers = app_state.slice(ProvidersStateSlice)
    registry = providers.registry
    if registry is None:
        return client
    return FailoverCompletionProvider(
        client,
        declared=declared,
        feature=feature,
        policy=FailoverPolicy(
            config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
            serviceability=providers.health_tracker,
        ),
        connections=registry.get,
        recorder=_failover_recorder(app_state),
    )


def _failover_recorder(app_state: AppState) -> FailoverRecorder | None:
    """Return the append-only sink for engagements, when persistence is up.

    Returns:
        The repository, or ``None`` before persistence connects. A missing
        sink degrades to the log alone rather than blocking a dispatch: the
        record is evidence about a call, not a precondition for making it.
    """
    backend = app_state.slice(PersistenceStateSlice).backend
    return None if backend is None else backend.provider_failover_events


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
    advertising an overlapping id that pick is ambiguous. Every case logs
    under *event* naming *subject* (the feature) and returns ``None`` so the
    caller fails loud or leaves the feature unwired.

    That includes there being no registry at all, which is an ordinary state
    of a deployment with nothing configured yet, and which every caller
    already handles as "unavailable". Reaching it through the raising
    accessor made a resolvable configuration problem exit the process: a
    deployment whose persisted providers could not be read had a model
    assignment it could not resolve, and the resulting boot crash restarted
    on a loop rather than coming up with the feature unwired.

    Returns:
        The driver for the ref's explicit provider, or ``None`` when the ref
        names no provider, names an unregistered one, or no registry is
        configured.
    """
    if not ref.provider.strip():
        logger.warning(
            event,
            note=f"{subject} model ref has no provider; a (provider, model) pair"
            " is required",
        )
        return None
    registry = app_state.slice(ProvidersStateSlice).registry
    if registry is None:
        logger.warning(
            event,
            note=f"{subject} model names a provider but no registry is"
            " configured, so nothing can be resolved against",
            provider=ref.provider,
        )
        return None
    try:
        return registry.get(ref.provider)
    except DriverNotRegisteredError:
        logger.warning(
            event,
            note=f"{subject} model's selected provider is not registered",
            provider=ref.provider,
        )
        return None
