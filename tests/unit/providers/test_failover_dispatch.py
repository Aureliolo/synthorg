"""Serving on the declared alternate: when, once, and never silently."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock

import pytest
import structlog

from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.provider import (
    PROVIDER_FAILOVER_ENGAGED,
    PROVIDER_FAILOVER_RECORD_FAILED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import (
    AuthenticationError,
    ProviderOverloadedError,
    ProviderPaymentRequiredError,
)
from synthorg.providers.failover import route_key
from synthorg.providers.failover_dispatch import (
    FailoverCompletionProvider,
    FailoverPolicy,
    retryable_on_alternate,
)
from synthorg.providers.failover_event import ProviderFailoverEvent
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderOutcomeClass,
    RecordSource,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
)
from synthorg.providers.serviceability import (
    ModelServiceability,
    ServiceabilityThresholds,
    aggregate_serviceability,
)
from synthorg.settings.model_ref import ModelRef
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_FEATURE = "engine.reasoning_model"
_DECLARED = ModelRef(provider="example-provider", model_id="example-expert-001")
_ALTERNATE = ModelRef(provider="test-provider", model_id="example-capable-001")
_MESSAGES = [ChatMessage(role=MessageRole.USER, content="hello")]


def _routes_json() -> str:
    return json.dumps({route_key(_DECLARED): _ALTERNATE.model_dump()})


def _resolver(
    *,
    enabled: bool = True,
    routes: str | None = None,
    retention_days: int = 90,
) -> ConfigResolver:
    """Answer the three live reads the policy makes."""
    return mock_of[ConfigResolver](
        get_bool=AsyncMock(return_value=enabled),
        get_str=AsyncMock(return_value=_routes_json() if routes is None else routes),
        get_int=AsyncMock(return_value=retention_days),
    )


def _enable_reads(resolver: ConfigResolver) -> int:
    """Return how many times the mechanism toggle has been read."""
    return cast("AsyncMock", resolver.get_bool).await_count


class _StubServiceability:
    """Returns one prepared window whatever pair it is asked about."""

    def __init__(self, view: ModelServiceability) -> None:
        self._view = view

    async def get_serviceability(
        self,
        provider_name: str,
        model: str | None,
        *,
        now: datetime | None = None,
        thresholds: ServiceabilityThresholds | None = None,
    ) -> ModelServiceability:
        del provider_name, model, now, thresholds
        return self._view


class _StubClient:
    """A completion client that records what it was asked for."""

    def __init__(self, *, name: str, raises: Exception | None = None) -> None:
        self.name = name
        self.raises = raises
        self.completed: list[str] = []
        self.streamed: list[str] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: object = None,
        config: object = None,
    ) -> CompletionResponse:
        del messages, tools, config
        self.completed.append(model)
        if self.raises is not None:
            raise self.raises
        return CompletionResponse(
            content=self.name,
            model=NotBlankStr(model),
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: object = None,
        config: object = None,
    ) -> AsyncIterator[StreamChunk]:
        del messages, tools, config
        self.streamed.append(model)
        if self.raises is not None:
            raise self.raises

        async def _chunks() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(content=self.name)

        return _chunks()

    async def get_model_capabilities(self, model: str) -> object:
        del model
        raise NotImplementedError

    async def batch_get_capabilities(self, models: tuple[str, ...]) -> object:
        del models
        raise NotImplementedError


class _RecordingSink:
    """Captures appended engagements and the retention cutoffs applied."""

    def __init__(self, *, fails: bool = False) -> None:
        self.events: list[ProviderFailoverEvent] = []
        self.cutoffs: list[datetime] = []
        self._fails = fails

    async def append(self, event: ProviderFailoverEvent) -> None:
        if self._fails:
            msg = "sink is down"
            raise RuntimeError(msg)
        self.events.append(event)

    async def purge_before(self, threshold: datetime) -> int:
        self.cutoffs.append(threshold)
        return 0


def _record(
    outcome: ProviderOutcomeClass, *, seconds_ago: float
) -> ProviderHealthRecord:
    succeeded = outcome is ProviderOutcomeClass.SUCCESS
    return ProviderHealthRecord(
        provider_name=NotBlankStr(_DECLARED.provider),
        model=NotBlankStr(_DECLARED.model_id),
        timestamp=_NOW - timedelta(seconds=seconds_ago),
        success=succeeded,
        response_time_ms=120.0,
        outcome_class=outcome,
        error_message=None if succeeded else f"{outcome.value} from upstream",
        source=RecordSource.REAL_CALL,
    )


def _window(*outcomes: ProviderOutcomeClass) -> ModelServiceability:
    records = [_record(o, seconds_ago=10.0 + i) for i, o in enumerate(outcomes)]
    return aggregate_serviceability(
        records,
        now=_NOW,
        thresholds=ServiceabilityThresholds(),
        provider_name=_DECLARED.provider,
        model=_DECLARED.model_id,
    )


_HEALTHY = (ProviderOutcomeClass.SUCCESS,) * 4
_OVERLOADED = (ProviderOutcomeClass.OVERLOADED,) * 4


def _provider(
    *,
    declared: _StubClient,
    alternate: _StubClient,
    resolver: ConfigResolver | None = None,
    window: ModelServiceability | None = None,
    sink: _RecordingSink | None = None,
) -> FailoverCompletionProvider:
    serviceability = None if window is None else _StubServiceability(window)
    return FailoverCompletionProvider(
        declared,
        declared=_DECLARED,
        feature=_FEATURE,
        policy=FailoverPolicy(
            config_resolver=resolver or _resolver(),
            serviceability=serviceability,
        ),
        connections=lambda name: alternate if name == _ALTERNATE.provider else declared,
        recorder=sink,
        clock=FakeClock(start=_NOW),
    )


class TestPreflight:
    async def test_an_unserviceable_declared_pair_is_not_tried(self) -> None:
        # The half that matters for cost and latency: paying the full retry
        # ladder against a pair already known to be refusing buys nothing.
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        wrapped = _provider(
            declared=declared, alternate=alternate, window=_window(*_OVERLOADED)
        )

        response = await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert declared.completed == []
        assert alternate.completed == [_ALTERNATE.model_id]
        assert response.content == "alternate"

    async def test_a_serving_declared_pair_is_used(self) -> None:
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        wrapped = _provider(
            declared=declared, alternate=alternate, window=_window(*_HEALTHY)
        )

        response = await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert declared.completed == [_DECLARED.model_id]
        assert alternate.completed == []
        assert response.content == "declared"

    async def test_a_silent_pair_is_not_a_trigger(self) -> None:
        # A pair nobody has called recently has said nothing about itself;
        # routing away from silence would move every idle feature across.
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        wrapped = _provider(declared=declared, alternate=alternate, window=_window())

        await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert declared.completed == [_DECLARED.model_id]

    async def test_an_empty_balance_routes_across(self) -> None:
        # A 402 does not decay: it stands until somebody pays, so the window
        # reads DOWN on it alone rather than averaging it against successes.
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        window = _window(
            ProviderOutcomeClass.SUCCESS,
            ProviderOutcomeClass.SUCCESS,
            ProviderOutcomeClass.SUCCESS,
            ProviderOutcomeClass.PAYMENT_REQUIRED,
        )
        wrapped = _provider(declared=declared, alternate=alternate, window=window)

        await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert alternate.completed == [_ALTERNATE.model_id]

    async def test_streaming_pre_flights(self) -> None:
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        wrapped = _provider(
            declared=declared, alternate=alternate, window=_window(*_OVERLOADED)
        )

        await wrapped.stream(_MESSAGES, _DECLARED.model_id)

        assert declared.streamed == []
        assert alternate.streamed == [_ALTERNATE.model_id]


class TestRetryOnce:
    async def test_a_retryable_failure_is_retried_on_the_alternate(self) -> None:
        declared = _StubClient(
            name="declared", raises=ProviderOverloadedError("queueing")
        )
        alternate = _StubClient(name="alternate")
        wrapped = _provider(
            declared=declared, alternate=alternate, window=_window(*_HEALTHY)
        )

        response = await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert declared.completed == [_DECLARED.model_id]
        assert alternate.completed == [_ALTERNATE.model_id]
        assert response.content == "alternate"

    async def test_a_request_level_failure_is_not_retried(self) -> None:
        # A bad key fails identically on the alternate, so the retry is pure
        # latency on top of a failure the caller already has.
        declared = _StubClient(name="declared", raises=AuthenticationError("bad key"))
        alternate = _StubClient(name="alternate")
        wrapped = _provider(
            declared=declared, alternate=alternate, window=_window(*_HEALTHY)
        )

        with pytest.raises(AuthenticationError):
            await wrapped.complete(_MESSAGES, _DECLARED.model_id)
        assert alternate.completed == []

    async def test_the_alternate_failing_too_surfaces_its_error(self) -> None:
        # A failover buys one more attempt, never a different answer.
        declared = _StubClient(name="declared", raises=ProviderOverloadedError("busy"))
        alternate = _StubClient(
            name="alternate", raises=ProviderPaymentRequiredError("empty")
        )
        wrapped = _provider(
            declared=declared, alternate=alternate, window=_window(*_HEALTHY)
        )

        with pytest.raises(ProviderPaymentRequiredError):
            await wrapped.complete(_MESSAGES, _DECLARED.model_id)

    async def test_streaming_does_not_retry(self) -> None:
        # A stream that failed partway has already handed chunks to the
        # caller; replaying elsewhere would splice two responses together.
        declared = _StubClient(name="declared", raises=ProviderOverloadedError("busy"))
        alternate = _StubClient(name="alternate")
        wrapped = _provider(
            declared=declared, alternate=alternate, window=_window(*_HEALTHY)
        )

        with pytest.raises(ProviderOverloadedError):
            await wrapped.stream(_MESSAGES, _DECLARED.model_id)
        assert declared.streamed == [_DECLARED.model_id]
        assert alternate.streamed == []


class TestDeclaration:
    async def test_disabled_never_engages(self) -> None:
        declared = _StubClient(name="declared", raises=ProviderOverloadedError("busy"))
        alternate = _StubClient(name="alternate")
        wrapped = _provider(
            declared=declared,
            alternate=alternate,
            resolver=_resolver(enabled=False),
            window=_window(*_OVERLOADED),
        )

        with pytest.raises(ProviderOverloadedError):
            await wrapped.complete(_MESSAGES, _DECLARED.model_id)
        assert alternate.completed == []

    async def test_an_undeclared_pair_never_engages(self) -> None:
        declared = _StubClient(name="declared", raises=ProviderOverloadedError("busy"))
        alternate = _StubClient(name="alternate")
        wrapped = _provider(
            declared=declared,
            alternate=alternate,
            resolver=_resolver(routes="{}"),
            window=_window(*_OVERLOADED),
        )

        with pytest.raises(ProviderOverloadedError):
            await wrapped.complete(_MESSAGES, _DECLARED.model_id)
        assert alternate.completed == []

    async def test_a_request_for_another_model_passes_through(self) -> None:
        # The wrapper knows one pair's alternate and nothing about anybody
        # else's, so a call naming a different model is delegated untouched.
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        resolver = _resolver()
        wrapped = _provider(
            declared=declared,
            alternate=alternate,
            resolver=resolver,
            window=_window(*_OVERLOADED),
        )

        await wrapped.complete(_MESSAGES, "some-other-model")

        assert declared.completed == ["some-other-model"]
        assert _enable_reads(resolver) == 0

    async def test_the_declaration_is_read_per_dispatch(self) -> None:
        # A route added mid-incident has to take effect on the next call, so
        # nothing about the declaration may be snapshotted at wiring time.
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        resolver = _resolver()
        wrapped = _provider(
            declared=declared,
            alternate=alternate,
            resolver=resolver,
            window=_window(*_HEALTHY),
        )

        await wrapped.complete(_MESSAGES, _DECLARED.model_id)
        await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert _enable_reads(resolver) == 2

    async def test_no_resolver_means_off(self) -> None:
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        wrapped = FailoverCompletionProvider(
            declared,
            declared=_DECLARED,
            feature=_FEATURE,
            policy=FailoverPolicy(),
            connections=lambda _: alternate,
            clock=FakeClock(start=_NOW),
        )

        await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert declared.completed == [_DECLARED.model_id]

    async def test_an_unregistered_alternate_leaves_the_declared_pair_serving(
        self,
    ) -> None:
        # A misconfigured alternate is operator-visible, not a reason to fail
        # a call the declared pair can still serve.
        declared = _StubClient(name="declared")

        def _absent(name: str) -> _StubClient:
            msg = f"{name} is not registered"
            raise LookupError(msg)

        wrapped = FailoverCompletionProvider(
            declared,
            declared=_DECLARED,
            feature=_FEATURE,
            policy=FailoverPolicy(
                config_resolver=_resolver(),
                serviceability=_StubServiceability(_window(*_OVERLOADED)),
            ),
            connections=_absent,
            clock=FakeClock(start=_NOW),
        )

        response = await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert response.content == "declared"


class TestNeverSilent:
    async def test_an_engagement_is_logged_and_persisted(self) -> None:
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        sink = _RecordingSink()
        wrapped = _provider(
            declared=declared,
            alternate=alternate,
            window=_window(*_OVERLOADED),
            sink=sink,
        )

        with structlog.testing.capture_logs() as logs:
            await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert [e["event"] for e in logs] == [PROVIDER_FAILOVER_ENGAGED]
        assert len(sink.events) == 1

    async def test_the_row_names_both_pairs_in_full(self) -> None:
        # "The alternate" identifies nothing once the route map has been
        # edited, so the row has to carry all four halves.
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        sink = _RecordingSink()
        wrapped = _provider(
            declared=declared,
            alternate=alternate,
            window=_window(*_OVERLOADED),
            sink=sink,
        )

        await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        event = sink.events[0]
        assert event.declared_provider == _DECLARED.provider
        assert event.declared_model == _DECLARED.model_id
        assert event.served_provider == _ALTERNATE.provider
        assert event.served_model == _ALTERNATE.model_id
        assert event.trigger_class is ProviderOutcomeClass.OVERLOADED
        assert event.trigger_stage == "preflight"
        assert event.feature == _FEATURE
        assert event.occurred_at == _NOW

    async def test_a_retry_engagement_records_its_stage(self) -> None:
        declared = _StubClient(name="declared", raises=ProviderOverloadedError("busy"))
        alternate = _StubClient(name="alternate")
        sink = _RecordingSink()
        wrapped = _provider(
            declared=declared,
            alternate=alternate,
            window=_window(*_HEALTHY),
            sink=sink,
        )

        await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert sink.events[0].trigger_stage == "retry"

    async def test_system_work_records_no_owner(self) -> None:
        # Outside a cost scope the dispatch belongs to no agent and no task,
        # and None is the honest value: an invented id names no row.
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        sink = _RecordingSink()
        wrapped = _provider(
            declared=declared,
            alternate=alternate,
            window=_window(*_OVERLOADED),
            sink=sink,
        )

        await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert sink.events[0].agent_id is None
        assert sink.events[0].task_id is None

    async def test_retention_is_applied_when_the_table_grows(self) -> None:
        # The table only ever grows at the moment an engagement is written,
        # so that is when the window is read and applied.
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        sink = _RecordingSink()
        wrapped = _provider(
            declared=declared,
            alternate=alternate,
            resolver=_resolver(retention_days=30),
            window=_window(*_OVERLOADED),
            sink=sink,
        )

        await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert sink.cutoffs == [_NOW - timedelta(days=30)]

    async def test_a_failed_record_does_not_fail_the_call(self) -> None:
        # The record is evidence about a call, not a precondition for it.
        declared = _StubClient(name="declared")
        alternate = _StubClient(name="alternate")
        wrapped = _provider(
            declared=declared,
            alternate=alternate,
            window=_window(*_OVERLOADED),
            sink=_RecordingSink(fails=True),
        )

        with structlog.testing.capture_logs() as logs:
            response = await wrapped.complete(_MESSAGES, _DECLARED.model_id)

        assert response.content == "alternate"
        assert PROVIDER_FAILOVER_RECORD_FAILED in {e["event"] for e in logs}


class TestRetryClassification:
    def test_a_connection_failure_is_retryable(self) -> None:
        assert (
            retryable_on_alternate(ProviderOverloadedError("busy"))
            is ProviderOutcomeClass.OVERLOADED
        )

    def test_an_auth_failure_is_not(self) -> None:
        assert retryable_on_alternate(AuthenticationError("bad key")) is None
