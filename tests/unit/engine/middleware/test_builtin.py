"""Tests for builtin middleware wrappers."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.engine.context import AgentContext
from synthorg.engine.middleware.builtin import (
    ApprovalGateMiddleware,
    CheckpointResumeMiddleware,
    ClassificationMiddleware,
    CostRecordingMiddleware,
    SanitizeMessageMiddleware,
    SecurityInterceptorMiddleware,
)
from synthorg.engine.middleware.models import (
    AgentMiddlewareContext,
    ModelCallResult,
    ToolCallResult,
)
from synthorg.engine.middleware.protocol import AgentMiddleware
from synthorg.providers.models import TokenUsage
from tests._shared import as_uuid

# ── Test helpers ──────────────────────────────────────────────────


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _mw_context() -> AgentMiddlewareContext:
    identity = _identity()
    ctx = AgentContext.from_identity(identity)
    task = Task(
        id=as_uuid("task-1"),
        title="Test task",
        description="A test task",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="test-project",
        created_by="test-creator",
    )
    return AgentMiddlewareContext(
        agent_context=ctx,
        identity=identity,
        task=task,
        agent_id=str(identity.id),
        task_id=str(task.id),
        execution_id="exec-1",
    )


def _token_usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5, cost=0.01)


async def _dummy_model_call(ctx: AgentMiddlewareContext) -> ModelCallResult:
    return ModelCallResult(
        response_text="hello",
        token_usage=_token_usage(),
        finish_reason="stop",
    )


async def _dummy_tool_call(ctx: AgentMiddlewareContext) -> ToolCallResult:
    return ToolCallResult(tool_name="test_tool", output="result")


# ── Protocol compliance ───────────────────────────────────────────


@pytest.mark.unit
class TestBuiltinProtocolCompliance:
    """All builtin middleware satisfy AgentMiddleware protocol."""

    @pytest.mark.parametrize(
        ("cls", "kwargs"),
        [
            (SecurityInterceptorMiddleware, {}),
            (SanitizeMessageMiddleware, {}),
            (ApprovalGateMiddleware, {}),
            (ClassificationMiddleware, {}),
            (CostRecordingMiddleware, {}),
            (CheckpointResumeMiddleware, {}),
        ],
    )
    def test_satisfies_protocol(self, cls: type, kwargs: dict[str, object]) -> None:
        mw = cls(**kwargs)
        assert isinstance(mw, AgentMiddleware)


# ── Name correctness ──────────────────────────────────────────────


@pytest.mark.unit
class TestBuiltinNames:
    """Each builtin has a unique, expected name."""

    def test_security_interceptor_name(self) -> None:
        assert SecurityInterceptorMiddleware().name == "security_interceptor"

    def test_sanitize_message_name(self) -> None:
        assert SanitizeMessageMiddleware().name == "sanitize_message"

    def test_approval_gate_name(self) -> None:
        assert ApprovalGateMiddleware().name == "approval_gate"

    def test_classification_name(self) -> None:
        assert ClassificationMiddleware().name == "classification"

    def test_cost_recording_name(self) -> None:
        assert CostRecordingMiddleware().name == "cost_recording"

    def test_checkpoint_resume_name(self) -> None:
        assert CheckpointResumeMiddleware().name == "checkpoint_resume"


# ── Hook delegation ───────────────────────────────────────────────


@pytest.mark.unit
class TestSecurityInterceptorMiddleware:
    """SecurityInterceptorMiddleware wraps tool calls."""

    async def test_delegates_tool_call(self) -> None:
        mw = SecurityInterceptorMiddleware()
        ctx = _mw_context()
        result = await mw.wrap_tool_call(ctx, _dummy_tool_call)
        assert result.tool_name == "test_tool"

    async def test_before_agent_noop(self) -> None:
        mw = SecurityInterceptorMiddleware()
        ctx = _mw_context()
        assert await mw.before_agent(ctx) is ctx


@pytest.mark.unit
class TestSanitizeMessageMiddleware:
    """SanitizeMessageMiddleware operates on before_model."""

    async def test_before_model_returns_context(self) -> None:
        mw = SanitizeMessageMiddleware()
        ctx = _mw_context()
        result = await mw.before_model(ctx)
        assert result is ctx


@pytest.mark.unit
class TestApprovalGateMiddleware:
    """ApprovalGateMiddleware operates on after_model."""

    async def test_after_model_returns_context(self) -> None:
        mw = ApprovalGateMiddleware()
        ctx = _mw_context()
        result = await mw.after_model(ctx)
        assert result is ctx

    async def test_with_none_gate(self) -> None:
        mw = ApprovalGateMiddleware(approval_gate=None)
        ctx = _mw_context()
        result = await mw.after_model(ctx)
        assert result is ctx


@pytest.mark.unit
class TestClassificationMiddleware:
    """ClassificationMiddleware wraps model and tool calls."""

    async def test_delegates_model_call(self) -> None:
        mw = ClassificationMiddleware()
        ctx = _mw_context()
        result = await mw.wrap_model_call(ctx, _dummy_model_call)
        assert result.response_text == "hello"

    async def test_delegates_tool_call(self) -> None:
        mw = ClassificationMiddleware()
        ctx = _mw_context()
        result = await mw.wrap_tool_call(ctx, _dummy_tool_call)
        assert result.tool_name == "test_tool"


@pytest.mark.unit
class TestCostRecordingMiddleware:
    """CostRecordingMiddleware operates on after_agent."""

    async def test_after_agent_returns_context(self) -> None:
        mw = CostRecordingMiddleware()
        ctx = _mw_context()
        result = await mw.after_agent(ctx)
        assert result is ctx

    async def test_with_none_tracker(self) -> None:
        mw = CostRecordingMiddleware(tracker=None)
        ctx = _mw_context()
        result = await mw.after_agent(ctx)
        assert result is ctx


@pytest.mark.unit
class TestCheckpointResumeMiddleware:
    """CheckpointResumeMiddleware operates on before_agent."""

    async def test_before_agent_returns_context(self) -> None:
        mw = CheckpointResumeMiddleware()
        ctx = _mw_context()
        result = await mw.before_agent(ctx)
        assert result is ctx

    async def test_with_repos(self) -> None:
        mw = CheckpointResumeMiddleware(
            checkpoint_repo=None,
            heartbeat_repo=None,
        )
        ctx = _mw_context()
        result = await mw.before_agent(ctx)
        assert result is ctx


# ── kwargs forwarding ─────────────────────────────────────────────


@pytest.mark.unit
class TestKwargsForwarding:
    """Builtin middleware accepts and ignores extra kwargs."""

    def test_security_interceptor_extra_kwargs(self) -> None:
        mw = SecurityInterceptorMiddleware(unknown_dep="x")
        assert mw.name == "security_interceptor"

    def test_cost_recording_extra_kwargs(self) -> None:
        mw = CostRecordingMiddleware(tracker=None, extra="y")
        assert mw.name == "cost_recording"

    def test_classification_extra_kwargs(self) -> None:
        mw = ClassificationMiddleware(
            error_taxonomy_config=None,
            extra="z",
        )
        assert mw.name == "classification"
