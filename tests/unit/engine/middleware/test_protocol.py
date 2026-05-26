"""Tests for agent middleware protocol, base class, and chain composition."""

from datetime import date
from typing import override
from uuid import uuid4

import pytest
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import Priority, TaskType
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.middleware.models import (
    AgentMiddlewareContext,
    ModelCallResult,
    ToolCallResult,
)
from synthorg.engine.middleware.protocol import (
    AgentMiddleware,
    AgentMiddlewareChain,
    BaseAgentMiddleware,
    ModelCallable,
    ToolCallable,
)
from synthorg.providers.models import TokenUsage

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


def _task() -> Task:
    return Task(
        id="task-1",
        title="Test task",
        description="A test task for middleware tests",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="test-project",
        created_by="test-creator",
    )


def _mw_context() -> AgentMiddlewareContext:
    identity = _identity()
    ctx = AgentContext.from_identity(identity)
    return AgentMiddlewareContext(
        agent_context=ctx,
        identity=identity,
        task=_task(),
        agent_id=str(identity.id),
        task_id="task-1",
        execution_id="exec-1",
    )


def _token_usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5, cost=0.01)


async def _dummy_model_call(
    ctx: AgentMiddlewareContext,
) -> ModelCallResult:
    return ModelCallResult(
        response_text="hello",
        token_usage=_token_usage(),
        finish_reason="stop",
    )


async def _dummy_tool_call(
    ctx: AgentMiddlewareContext,
) -> ToolCallResult:
    return ToolCallResult(tool_name="test_tool", output="result")


# ── Protocol compliance ───────────────────────────────────────────


@pytest.mark.unit
class TestProtocolCompliance:
    """BaseAgentMiddleware satisfies the AgentMiddleware protocol."""

    def test_base_is_agent_middleware(self) -> None:
        mw = BaseAgentMiddleware(name="test")
        assert isinstance(mw, AgentMiddleware)

    def test_base_name(self) -> None:
        mw = BaseAgentMiddleware(name="my_middleware")
        assert mw.name == "my_middleware"

    def test_custom_subclass_is_agent_middleware(self) -> None:
        class Custom(BaseAgentMiddleware):
            pass

        mw = Custom(name="custom")
        assert isinstance(mw, AgentMiddleware)


# ── Base class no-op behavior ─────────────────────────────────────


@pytest.mark.unit
class TestBaseAgentMiddleware:
    """No-op defaults return context unchanged and delegate calls."""

    async def test_before_agent_returns_same_context(self) -> None:
        mw = BaseAgentMiddleware(name="test")
        ctx = _mw_context()
        result = await mw.before_agent(ctx)
        assert result is ctx

    async def test_before_model_returns_same_context(self) -> None:
        mw = BaseAgentMiddleware(name="test")
        ctx = _mw_context()
        result = await mw.before_model(ctx)
        assert result is ctx

    async def test_after_model_returns_same_context(self) -> None:
        mw = BaseAgentMiddleware(name="test")
        ctx = _mw_context()
        result = await mw.after_model(ctx)
        assert result is ctx

    async def test_after_agent_returns_same_context(self) -> None:
        mw = BaseAgentMiddleware(name="test")
        ctx = _mw_context()
        result = await mw.after_agent(ctx)
        assert result is ctx

    async def test_wrap_model_call_delegates(self) -> None:
        mw = BaseAgentMiddleware(name="test")
        ctx = _mw_context()
        result = await mw.wrap_model_call(ctx, _dummy_model_call)
        assert result.response_text == "hello"

    async def test_wrap_tool_call_delegates(self) -> None:
        mw = BaseAgentMiddleware(name="test")
        ctx = _mw_context()
        result = await mw.wrap_tool_call(ctx, _dummy_tool_call)
        assert result.tool_name == "test_tool"


# ── Chain composition ─────────────────────────────────────────────


class _TrackingMiddleware(BaseAgentMiddleware):
    """Records hook invocations for testing composition order."""

    def __init__(self, *, name: str, log: list[str]) -> None:
        super().__init__(name=name)
        self._log = log

    @override
    async def before_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        self._log.append(f"{self.name}:before_agent")
        return ctx

    @override
    async def before_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        self._log.append(f"{self.name}:before_model")
        return ctx

    @override
    async def after_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        self._log.append(f"{self.name}:after_model")
        return ctx

    @override
    async def after_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        self._log.append(f"{self.name}:after_agent")
        return ctx

    @override
    async def wrap_model_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ModelCallable,
    ) -> ModelCallResult:
        self._log.append(f"{self.name}:wrap_model_call:enter")
        result = await call(ctx)
        self._log.append(f"{self.name}:wrap_model_call:exit")
        return result

    @override
    async def wrap_tool_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ToolCallable,
    ) -> ToolCallResult:
        self._log.append(f"{self.name}:wrap_tool_call:enter")
        result = await call(ctx)
        self._log.append(f"{self.name}:wrap_tool_call:exit")
        return result


@pytest.mark.unit
class TestAgentMiddlewareChain:
    """Chain composition rules: linear before_*/after_*, onion wrap_*."""

    def test_empty_chain(self) -> None:
        chain = AgentMiddlewareChain()
        assert len(chain) == 0
        assert chain.names == ()

    def test_names(self) -> None:
        chain = AgentMiddlewareChain(
            (
                BaseAgentMiddleware(name="a"),
                BaseAgentMiddleware(name="b"),
            )
        )
        assert chain.names == ("a", "b")

    def test_rejects_duplicate_names(self) -> None:
        from synthorg.engine.middleware.errors import MiddlewareConfigError

        with pytest.raises(MiddlewareConfigError, match="Duplicate"):
            AgentMiddlewareChain(
                (
                    BaseAgentMiddleware(name="a"),
                    BaseAgentMiddleware(name="a"),
                )
            )

    async def test_before_agent_left_to_right(self) -> None:
        log: list[str] = []
        chain = AgentMiddlewareChain(
            (
                _TrackingMiddleware(name="a", log=log),
                _TrackingMiddleware(name="b", log=log),
                _TrackingMiddleware(name="c", log=log),
            )
        )
        ctx = _mw_context()
        await chain.run_before_agent(ctx)
        assert log == [
            "a:before_agent",
            "b:before_agent",
            "c:before_agent",
        ]

    async def test_before_model_left_to_right(self) -> None:
        log: list[str] = []
        chain = AgentMiddlewareChain(
            (
                _TrackingMiddleware(name="a", log=log),
                _TrackingMiddleware(name="b", log=log),
            )
        )
        ctx = _mw_context()
        await chain.run_before_model(ctx)
        assert log == ["a:before_model", "b:before_model"]

    async def test_after_model_right_to_left(self) -> None:
        log: list[str] = []
        chain = AgentMiddlewareChain(
            (
                _TrackingMiddleware(name="a", log=log),
                _TrackingMiddleware(name="b", log=log),
                _TrackingMiddleware(name="c", log=log),
            )
        )
        ctx = _mw_context()
        await chain.run_after_model(ctx)
        assert log == [
            "c:after_model",
            "b:after_model",
            "a:after_model",
        ]

    async def test_after_agent_right_to_left(self) -> None:
        log: list[str] = []
        chain = AgentMiddlewareChain(
            (
                _TrackingMiddleware(name="a", log=log),
                _TrackingMiddleware(name="b", log=log),
            )
        )
        ctx = _mw_context()
        await chain.run_after_agent(ctx)
        assert log == ["b:after_agent", "a:after_agent"]

    async def test_wrap_model_call_onion_order(self) -> None:
        log: list[str] = []
        chain = AgentMiddlewareChain(
            (
                _TrackingMiddleware(name="outer", log=log),
                _TrackingMiddleware(name="inner", log=log),
            )
        )
        ctx = _mw_context()

        async def core_model_call(
            _ctx: AgentMiddlewareContext,
        ) -> ModelCallResult:
            log.append("core")
            return ModelCallResult(
                response_text="ok",
                token_usage=_token_usage(),
                finish_reason="stop",
            )

        await chain.run_wrap_model_call(ctx, core_model_call)
        assert log == [
            "outer:wrap_model_call:enter",
            "inner:wrap_model_call:enter",
            "core",
            "inner:wrap_model_call:exit",
            "outer:wrap_model_call:exit",
        ]

    async def test_wrap_tool_call_onion_order(self) -> None:
        log: list[str] = []
        chain = AgentMiddlewareChain(
            (
                _TrackingMiddleware(name="outer", log=log),
                _TrackingMiddleware(name="inner", log=log),
            )
        )
        ctx = _mw_context()

        async def core_tool_call(
            _ctx: AgentMiddlewareContext,
        ) -> ToolCallResult:
            log.append("core")
            return ToolCallResult(tool_name="t", output="ok")

        await chain.run_wrap_tool_call(ctx, core_tool_call)
        assert log == [
            "outer:wrap_tool_call:enter",
            "inner:wrap_tool_call:enter",
            "core",
            "inner:wrap_tool_call:exit",
            "outer:wrap_tool_call:exit",
        ]

    async def test_empty_chain_delegates_model_call(self) -> None:
        chain = AgentMiddlewareChain()
        ctx = _mw_context()
        result = await chain.run_wrap_model_call(
            ctx,
            _dummy_model_call,
        )
        assert result.response_text == "hello"

    async def test_empty_chain_delegates_tool_call(self) -> None:
        chain = AgentMiddlewareChain()
        ctx = _mw_context()
        result = await chain.run_wrap_tool_call(
            ctx,
            _dummy_tool_call,
        )
        assert result.tool_name == "test_tool"


# ── Error propagation ─────────────────────────────────────────────


class _ErrorMiddleware(BaseAgentMiddleware):
    """Raises in before_agent to test error propagation."""

    @override
    async def before_agent(self, ctx: AgentMiddlewareContext) -> AgentMiddlewareContext:
        msg = f"Error from {self.name}"
        raise RuntimeError(msg)


@pytest.mark.unit
class TestChainErrorPropagation:
    """Middleware exceptions propagate without catching."""

    async def test_before_agent_error_propagates(self) -> None:
        chain = AgentMiddlewareChain((_ErrorMiddleware(name="bad"),))
        ctx = _mw_context()
        with pytest.raises(RuntimeError, match="Error from bad"):
            await chain.run_before_agent(ctx)

    async def test_wrap_model_call_error_propagates(self) -> None:
        class _WrapError(BaseAgentMiddleware):
            @override
            async def wrap_model_call(
                self,
                ctx: AgentMiddlewareContext,
                call: ModelCallable,
            ) -> ModelCallResult:
                msg = "wrap error"
                raise ValueError(msg)

        chain = AgentMiddlewareChain((_WrapError(name="bad"),))
        ctx = _mw_context()
        with pytest.raises(ValueError, match="wrap error"):
            await chain.run_wrap_model_call(ctx, _dummy_model_call)

    async def test_error_stops_chain(self) -> None:
        """Error in first middleware prevents second from running."""
        log: list[str] = []
        chain = AgentMiddlewareChain(
            (
                _ErrorMiddleware(name="bad"),
                _TrackingMiddleware(name="good", log=log),
            )
        )
        ctx = _mw_context()
        with pytest.raises(RuntimeError):
            await chain.run_before_agent(ctx)
        assert log == []  # second middleware never ran


# ── Context metadata ──────────────────────────────────────────────


@pytest.mark.unit
class TestAgentMiddlewareContext:
    """AgentMiddlewareContext model behavior."""

    def test_frozen(self) -> None:
        ctx = _mw_context()
        with pytest.raises(ValidationError):
            ctx.agent_id = "other"  # type: ignore[misc]

    def test_with_metadata(self) -> None:
        ctx = _mw_context()
        updated = ctx.with_metadata("key", "value")
        assert updated.metadata["key"] == "value"
        assert "key" not in ctx.metadata  # original unchanged

    def test_with_metadata_preserves_existing(self) -> None:
        ctx = _mw_context().with_metadata("a", 1)
        updated = ctx.with_metadata("b", 2)
        assert updated.metadata["a"] == 1
        assert updated.metadata["b"] == 2

    def test_metadata_defensive_copy(self) -> None:
        """Modifying the input dict does not affect the frozen context."""
        identity = _identity()
        ctx_inner = AgentContext.from_identity(identity)
        input_dict = {"key": "value"}
        ctx = AgentMiddlewareContext(
            agent_context=ctx_inner,
            identity=identity,
            task=_task(),
            agent_id=str(identity.id),
            task_id="task-1",
            execution_id="exec-1",
            metadata=input_dict,
        )
        input_dict["key"] = "mutated"
        assert ctx.metadata["key"] == "value"

    def test_default_metadata_is_empty(self) -> None:
        ctx = _mw_context()
        assert dict(ctx.metadata) == {}
