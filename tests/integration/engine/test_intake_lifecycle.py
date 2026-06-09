"""Integration tests for the intake engine lifecycle."""

from typing import cast

import pytest

from synthorg.client.models import (
    ClientRequest,
    RequestStatus,
    TaskRequirement,
)
from synthorg.engine.intake import (
    AgentIntake,
    DirectIntake,
    IntakeEngine,
    IntakeResult,
    IntakeStrategy,
    TaskCreator,
)
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider

pytestmark = pytest.mark.integration


class _FakeTask:
    def __init__(self, *, task_id: str) -> None:
        self.id = task_id


class _FakeTaskEngine:
    def __init__(self, *, next_id: str = "task-created") -> None:
        self.next_id = next_id
        self.captured_data: object = None
        self.captured_requested_by: str | None = None

    async def create_task(self, data: object, *, requested_by: str) -> _FakeTask:
        self.captured_data = data
        self.captured_requested_by = requested_by
        return _FakeTask(task_id=self.next_id)


def _request(*, title: str = "Build something") -> ClientRequest:
    return ClientRequest(
        client_id="client-1",
        requirement=TaskRequirement(
            title=title,
            description=f"{title} for the customer.",
        ),
    )


class _AcceptingStrategy:
    """Test strategy that always returns an accepted result."""

    def __init__(self, *, task_id: str = "task-1") -> None:
        self._task_id = task_id

    async def process(self, request: ClientRequest) -> IntakeResult:
        return IntakeResult.accepted_result(
            request_id=request.request_id,
            task_id=self._task_id,
        )


class _RejectingStrategy:
    def __init__(self, *, reason: str = "not ready") -> None:
        self._reason = reason

    async def process(self, request: ClientRequest) -> IntakeResult:
        return IntakeResult.rejected_result(
            request_id=request.request_id,
            reason=self._reason,
        )


class TestIntakeEngineLifecycle:
    async def test_accepted_request_walks_full_lifecycle(self) -> None:
        engine = IntakeEngine(strategy=_AcceptingStrategy(task_id="t1"))
        request = _request()
        final, result = await engine.process(request)

        assert final.status is RequestStatus.TASK_CREATED
        assert final.metadata["task_id"] == "t1"
        assert result.accepted is True
        assert result.task_id == "t1"

    async def test_rejected_request_ends_in_cancelled(self) -> None:
        engine = IntakeEngine(strategy=_RejectingStrategy(reason="out of scope"))
        request = _request()
        final, result = await engine.process(request)

        assert final.status is RequestStatus.CANCELLED
        assert final.metadata["rejection_reason"] == "out of scope"
        assert result.accepted is False
        assert result.rejection_reason == "out of scope"

    async def test_only_accepts_submitted_requests(self) -> None:
        engine = IntakeEngine(strategy=_AcceptingStrategy())
        already_triaging = _request().with_status(RequestStatus.TRIAGING)
        with pytest.raises(ValueError, match="SUBMITTED"):
            await engine.process(already_triaging)

    async def test_strategy_protocol_compatible(self) -> None:
        assert isinstance(_AcceptingStrategy(), IntakeStrategy)
        assert isinstance(_RejectingStrategy(), IntakeStrategy)


class TestDirectIntake:
    async def test_creates_task_on_accept(self) -> None:
        task_engine = _FakeTaskEngine(next_id="task-xyz")
        strategy = DirectIntake(
            task_engine=cast(TaskCreator, task_engine),
            project="sim",
        )
        result = await strategy.process(_request(title="Build feature"))
        assert result.accepted is True
        assert result.task_id == "task-xyz"
        assert task_engine.captured_requested_by == "intake-direct"

    async def test_full_lifecycle_with_direct(self) -> None:
        task_engine = _FakeTaskEngine(next_id="task-abc")
        strategy = DirectIntake(
            task_engine=cast(TaskCreator, task_engine),
        )
        engine = IntakeEngine(strategy=strategy)
        final, result = await engine.process(_request())
        assert final.status is RequestStatus.TASK_CREATED
        assert result.accepted is True
        assert final.metadata["task_id"] == "task-abc"


class _StubProvider:
    def __init__(self, *, content: str) -> None:
        self._content = content
        self.captured_messages: list[ChatMessage] | None = None

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        del tools, config
        self.captured_messages = messages
        return CompletionResponse(
            content=self._content,
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=5, output_tokens=5, cost=0.0),
            model=model,
        )


class TestAgentIntake:
    async def test_accepts_when_provider_returns_accept(self) -> None:
        provider = _StubProvider(
            content='{"accepted": true, "refined_title": "Sharper title"}'
        )
        task_engine = _FakeTaskEngine(next_id="task-ag")
        strategy = AgentIntake(
            task_engine=cast(TaskCreator, task_engine),
            provider=cast(CompletionProvider, provider),
            model="test-model",
        )
        result = await strategy.process(_request())
        assert result.accepted is True
        assert result.task_id == "task-ag"

    async def test_rejects_when_provider_says_no(self) -> None:
        provider = _StubProvider(
            content='{"accepted": false, "reason": "missing requirements"}'
        )
        task_engine = _FakeTaskEngine()
        strategy = AgentIntake(
            task_engine=cast(TaskCreator, task_engine),
            provider=cast(CompletionProvider, provider),
            model="test-model",
        )
        result = await strategy.process(_request())
        assert result.accepted is False
        assert result.rejection_reason == "missing requirements"
        assert task_engine.captured_data is None

    async def test_rejects_on_malformed_response(self) -> None:
        provider = _StubProvider(content="not json")
        task_engine = _FakeTaskEngine()
        strategy = AgentIntake(
            task_engine=cast(TaskCreator, task_engine),
            provider=cast(CompletionProvider, provider),
            model="test-model",
        )
        result = await strategy.process(_request())
        assert result.accepted is False
        assert result.rejection_reason is not None
        assert "malformed" in result.rejection_reason

    async def test_rejects_with_default_reason_on_accepted_false(
        self,
    ) -> None:
        provider = _StubProvider(content='{"accepted": false}')
        task_engine = _FakeTaskEngine()
        strategy = AgentIntake(
            task_engine=cast(TaskCreator, task_engine),
            provider=cast(CompletionProvider, provider),
            model="test-model",
        )
        result = await strategy.process(_request())
        assert result.accepted is False
        assert result.rejection_reason is not None

    async def test_refines_when_provider_supplies_new_title(
        self,
    ) -> None:
        provider = _StubProvider(
            content=(
                '{"accepted": true, "refined_title": "Better title", '
                '"refined_description": "Better description"}'
            )
        )
        task_engine = _FakeTaskEngine(next_id="task-r")
        strategy = AgentIntake(
            task_engine=cast(TaskCreator, task_engine),
            provider=cast(CompletionProvider, provider),
            model="test-model",
        )
        result = await strategy.process(_request(title="Old title"))
        assert result.accepted is True
        created = task_engine.captured_data
        assert created is not None
        assert getattr(created, "title", None) == "Better title"


# Satisfy linters that want MessageRole to be referenced.
_ = MessageRole
