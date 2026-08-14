# module-kind: service
"""Serving a system feature's request on the alternate its operator declared.

Two triggers, and they answer different halves of the same incident:

- **Pre-flight.** The declared pair's recent window already reads
  unserviceable, so it is not tried. This is the half that matters for cost
  and latency: the incident that motivated the feature had a pair taking up
  to 311 seconds to refuse a five-token reply, and paying the full retry
  ladder against it on every call is the expensive way to learn nothing.
- **Retry once.** The pair still looked serviceable and the call failed
  anyway, on a class the alternate has a real chance of surviving. Exactly
  one retry: a second failure is the answer, and a ladder across connections
  would multiply the latency the pre-flight half exists to avoid.

Nothing here picks a provider. The alternate comes from an exact-key lookup
in the map an operator authored (:mod:`synthorg.providers.failover`), which
is what keeps the carve-out from becoming the auto-pick this codebase spent
a lot of effort removing. Nothing here reaches an agent's own pair, the
gateway or an embedder either: this wraps the client a ``MODEL_REF`` system
feature resolved, and those three resolve elsewhere.
"""

from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_FAILOVER_ENGAGED,
    PROVIDER_FAILOVER_RECORD_FAILED,
    PROVIDER_FAILOVER_UNAVAILABLE,
)
from synthorg.providers.agent_availability import (
    ServiceabilityReader,
    unavailability_from,
)
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.cost_recording import current_cost_context
from synthorg.providers.errors import ProviderError, classify_provider_error
from synthorg.providers.failover import RETRYABLE_ON_ALTERNATE, parse_failover_routes
from synthorg.providers.failover_event import FailoverStage, ProviderFailoverEvent
from synthorg.providers.health import ProviderOutcomeClass
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider, ConnectionSelector
from synthorg.providers.serviceability import dominant_failure
from synthorg.providers.serviceability_settings import (
    resolve_serviceability_thresholds,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.model_ref import ModelRef
from synthorg.settings.registry import registered_default_int
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_ENABLED_KEY = "failover_enabled"
_ROUTES_KEY = "failover_routes"
_RETENTION_KEY = "failover_event_retention_days"


@runtime_checkable
class FailoverRecorder(Protocol):
    """Persists one engagement so it outlives the process that served it.

    Structurally the append-only repository's own surface, so the wiring
    layer hands the repository straight in rather than through an adapter
    whose whole body would be one forwarded call.
    """

    async def append(self, event: ProviderFailoverEvent, /) -> None:
        """Persist one engagement."""
        ...

    async def purge_before(self, threshold: datetime, /) -> int:
        """Drop engagements older than *threshold*."""
        ...


class FailoverPolicy:
    """Reads, per dispatch, whether a declared pair has an alternate to use.

    Every read is live. The mechanism toggle and the route map are both
    operator state that changes mid-incident, which is exactly when this
    runs, so a snapshot taken at wiring time would answer with the world as
    it was before the outage began.

    Args:
        config_resolver: Live settings read, or ``None`` when the consumer
            was built without one (test harness, anonymous boot), in which
            case there is no declaration to read and failover stays off.
        serviceability: Source of the recent-window view per pair, or
            ``None`` when no health tracker is wired; without it there is no
            pre-flight signal and only the retry half can fire.
    """

    __slots__ = ("_config_resolver", "_serviceability")

    def __init__(
        self,
        *,
        config_resolver: ConfigResolver | None = None,
        serviceability: ServiceabilityReader | None = None,
    ) -> None:
        self._config_resolver = config_resolver
        self._serviceability = serviceability

    async def alternate_for(self, declared: ModelRef) -> ModelRef | None:
        """Return the alternate declared for *declared*, or ``None``.

        Returns:
            The operator's alternate pair when the mechanism is on and a
            route names this exact pair; ``None`` otherwise, which reads the
            same as the feature being off.
        """
        if self._config_resolver is None:
            return None
        if not await self._config_resolver.get_bool(
            SettingNamespace.PROVIDERS.value, _ENABLED_KEY
        ):
            return None
        raw = await self._config_resolver.get_str(
            SettingNamespace.PROVIDERS.value, _ROUTES_KEY
        )
        return parse_failover_routes(raw).alternate_for(declared)

    async def preflight_trigger(
        self,
        declared: ModelRef,
        *,
        now: datetime | None = None,
    ) -> ProviderOutcomeClass | None:
        """Return why *declared* should not be tried, or ``None``.

        Returns:
            The failure class dominating the pair's recent window when that
            window reads unserviceable, else ``None``. An UNKNOWN verdict is
            never a trigger: a pair nobody has called recently has said
            nothing about itself, and routing away from silence would move
            every idle feature onto its alternate.
        """
        if self._serviceability is None:
            return None
        view = await self._serviceability.get_serviceability(
            declared.provider,
            declared.model_id,
            now=now,
            thresholds=await resolve_serviceability_thresholds(self._config_resolver),
        )
        if unavailability_from(view) is None:
            return None
        return dominant_failure(view) or ProviderOutcomeClass.OTHER

    async def retention_cutoff(self, now: datetime) -> datetime:
        """Return the instant before which engagements may be dropped.

        Returns:
            ``now`` less the configured window, falling back to the
            registered default when there is no resolver to read: the table
            grows with incidents, and an unreadable setting is not a reason
            to keep every row an installation ever wrote.
        """
        days = registered_default_int(SettingNamespace.PROVIDERS.value, _RETENTION_KEY)
        if self._config_resolver is not None:
            resolved = await self._config_resolver.get_int(
                SettingNamespace.PROVIDERS.value, _RETENTION_KEY
            )
            if resolved >= 1:
                days = resolved
        return now - timedelta(days=days)


def retryable_on_alternate(exc: BaseException) -> ProviderOutcomeClass | None:
    """Return the outcome class when *exc* is worth retrying elsewhere.

    Returns:
        The class that failed, or ``None`` when the alternate would fail
        identically (an invalid request, a bad key, a content filter, an
        unknown model), in which case a retry is pure latency on top of a
        failure the caller already has.
    """
    outcome = ProviderOutcomeClass.for_error(classify_provider_error(exc))
    return outcome if outcome in RETRYABLE_ON_ALTERNATE else None


class FailoverCompletionProvider:
    """The declared connection's client, with the operator's alternate behind it.

    Wraps a resolved :class:`BoundCompletion` client rather than sitting on a
    call site, so every ``MODEL_REF`` system feature is covered without any
    of them threading a dispatcher through. A request naming a model other
    than the declared one is delegated untouched: this decorator knows what
    one pair's alternate is, and nothing about anybody else's.

    Args:
        declared_client: Client for the connection the operator bound.
        declared: The bound pair, which is what a route is keyed on.
        feature: The setting the pair came from, recorded so an operator can
            tell which capability was affected.
        policy: Live read of the declaration.
        connections: Resolves the alternate's connection to a client.
        recorder: Persists each engagement, or ``None`` when persistence is
            not wired (the event still logs).
        clock: Time source for the recorded instant.
    """

    __slots__ = (
        "_clock",
        "_connections",
        "_declared",
        "_declared_client",
        "_feature",
        "_policy",
        "_recorder",
    )

    def __init__(
        self,
        declared_client: CompletionProvider,
        *,
        declared: ModelRef,
        feature: str,
        policy: FailoverPolicy,
        connections: ConnectionSelector,
        recorder: FailoverRecorder | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._declared_client = declared_client
        self._declared = declared
        self._feature = feature
        self._policy = policy
        self._connections = connections
        self._recorder = recorder
        self._clock = clock or SystemClock()

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Complete on the declared pair, or on its alternate.

        Returns:
            The response from whichever connection served it. Cost is
            attributed by the driver that ran the call, so a record naming
            the alternate is what a served failover produces.

        Raises:
            ProviderError: If the declared pair fails on a class the
                alternate would fail identically on, or if the one retry on
                the alternate fails too. A failover buys one more attempt,
                never a different answer to give the caller.
        """
        resolved = await self._alternate_for(model)
        if resolved is None:
            return await self._declared_client.complete(
                messages, model, tools=tools, config=config
            )
        alternate, client = resolved
        trigger = await self._policy.preflight_trigger(self._declared)
        if trigger is not None:
            await self._engage(alternate, trigger=trigger, stage="preflight")
            return await client.complete(
                messages, alternate.model_id, tools=tools, config=config
            )
        try:
            return await self._declared_client.complete(
                messages, model, tools=tools, config=config
            )
        except ProviderError as exc:
            retry_class = retryable_on_alternate(exc)
            if retry_class is None:
                raise
            await self._engage(alternate, trigger=retry_class, stage="retry")
            return await client.complete(
                messages, alternate.model_id, tools=tools, config=config
            )

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream from the declared pair, or from its alternate.

        Only the pre-flight half applies. A stream that fails partway has
        already handed chunks to the caller, and replaying it elsewhere would
        deliver the opening of one response followed by the whole of another.

        Returns:
            The chunk iterator from whichever connection served it.
        """
        resolved = await self._alternate_for(model)
        if resolved is None:
            return await self._declared_client.stream(
                messages, model, tools=tools, config=config
            )
        alternate, client = resolved
        trigger = await self._policy.preflight_trigger(self._declared)
        if trigger is None:
            return await self._declared_client.stream(
                messages, model, tools=tools, config=config
            )
        await self._engage(alternate, trigger=trigger, stage="preflight")
        return await client.stream(
            messages, alternate.model_id, tools=tools, config=config
        )

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        """Return the declared connection's capability metadata.

        Capabilities describe a model on a connection, not a call, so they
        are read where the operator bound it. Answering from the alternate
        would describe a model the caller is not asking about.

        Returns:
            Static capability and cost information.
        """
        return await self._declared_client.get_model_capabilities(model)

    async def batch_get_capabilities(
        self,
        models: tuple[str, ...],
    ) -> Mapping[str, ModelCapabilities | None]:
        """Return the declared connection's capability metadata for many models.

        Returns:
            Mapping from model id to capabilities, or ``None`` per failure.
        """
        return await self._declared_client.batch_get_capabilities(models)

    async def _alternate_for(
        self,
        model: str,
    ) -> tuple[ModelRef, CompletionProvider] | None:
        """Return the alternate and its client, or ``None`` to pass through.

        The client is returned rather than resolved again at the dispatch
        site. This module reads all operator state live, so the registry can
        be replaced by a settings reload between the two calls, and a second
        resolution that raised would propagate a registry error for a request
        the declared pair could still serve.

        Returns:
            The declared alternate and the client that serves it, when this
            request names the bound pair and that connection resolves;
            ``None`` when the request names a different model, no route is
            declared, or the alternate's connection is not registered (which
            is an operator-visible misconfiguration, not a reason to fail a
            call the declared pair can still serve).
        """
        if model.strip() != self._declared.model_id.strip():
            return None
        alternate = await self._policy.alternate_for(self._declared)
        if alternate is None:
            return None
        try:
            client = self._connections(alternate.provider)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- an unregistered alternate is an
            # operator-visible misconfiguration, not a reason to fail a
            # call the declared pair can still serve
            reraise_critical(exc)
            logger.warning(
                PROVIDER_FAILOVER_UNAVAILABLE,
                feature=self._feature,
                declared_provider=self._declared.provider,
                alternate_provider=alternate.provider,
                reason="alternate_connection_unregistered",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        return alternate, client

    async def _engage(
        self,
        alternate: ModelRef,
        *,
        trigger: ProviderOutcomeClass,
        stage: FailoverStage,
    ) -> None:
        """Announce and persist one engagement before the alternate serves."""
        context = current_cost_context()
        event = ProviderFailoverEvent(
            occurred_at=self._clock.now(),
            feature=NotBlankStr(self._feature),
            declared_provider=NotBlankStr(self._declared.provider),
            declared_model=NotBlankStr(self._declared.model_id),
            served_provider=NotBlankStr(alternate.provider),
            served_model=NotBlankStr(alternate.model_id),
            trigger_class=trigger,
            trigger_stage=stage,
            agent_id=None if context is None else context.agent_id,
            task_id=None if context is None else context.task_id,
        )
        logger.warning(
            PROVIDER_FAILOVER_ENGAGED,
            feature=self._feature,
            declared_provider=self._declared.provider,
            declared_model=self._declared.model_id,
            served_provider=alternate.provider,
            served_model=alternate.model_id,
            trigger_class=trigger.value,
            trigger_stage=stage,
        )
        if self._recorder is None:
            return
        try:
            await self._recorder.append(event)
            # Trimmed here rather than by a scheduled sweep: an engagement is
            # an exceptional event, so this table only ever grows at the
            # moment one is written, and a daily loop would spend every other
            # tick proving a table nobody appended to is still within its
            # window. Retention is therefore read and applied exactly when it
            # can matter.
            await self._recorder.purge_before(
                await self._policy.retention_cutoff(event.occurred_at)
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- the record is evidence; losing it
            # must not fail the call it describes
            reraise_critical(exc)
            logger.warning(
                PROVIDER_FAILOVER_RECORD_FAILED,
                feature=self._feature,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


__all__ = [
    "FailoverCompletionProvider",
    "FailoverPolicy",
    "FailoverRecorder",
    "retryable_on_alternate",
]
