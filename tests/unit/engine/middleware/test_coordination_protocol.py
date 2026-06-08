"""Tests for coordination middleware protocol, base class, and chain."""

from datetime import date
from typing import override
from uuid import uuid4

import pytest
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.engine.coordination.models import CoordinationContext
from synthorg.engine.middleware.coordination_protocol import (
    BaseCoordinationMiddleware,
    CoordinationMiddleware,
    CoordinationMiddlewareChain,
    CoordinationMiddlewareContext,
)
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


def _task() -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Test task",
        description="A test task for coordination middleware tests",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="test-project",
        created_by="test-creator",
    )


def _coord_context() -> CoordinationContext:
    return CoordinationContext(
        task=_task(),
        available_agents=(_identity(),),
    )


def _mw_context() -> CoordinationMiddlewareContext:
    return CoordinationMiddlewareContext(
        coordination_context=_coord_context(),
    )


# ── Protocol compliance ───────────────────────────────────────────


@pytest.mark.unit
class TestProtocolCompliance:
    """BaseCoordinationMiddleware satisfies CoordinationMiddleware."""

    def test_base_is_coordination_middleware(self) -> None:
        mw = BaseCoordinationMiddleware(name="test")
        assert isinstance(mw, CoordinationMiddleware)

    def test_base_name(self) -> None:
        mw = BaseCoordinationMiddleware(name="my_mw")
        assert mw.name == "my_mw"

    def test_custom_subclass(self) -> None:
        class Custom(BaseCoordinationMiddleware):
            pass

        mw = Custom(name="custom")
        assert isinstance(mw, CoordinationMiddleware)


# ── Base class no-op behavior ─────────────────────────────────────


@pytest.mark.unit
class TestBaseCoordinationMiddleware:
    """No-op defaults return context unchanged."""

    async def test_before_decompose(self) -> None:
        mw = BaseCoordinationMiddleware(name="test")
        ctx = _mw_context()
        assert await mw.before_decompose(ctx) is ctx

    async def test_after_decompose(self) -> None:
        mw = BaseCoordinationMiddleware(name="test")
        ctx = _mw_context()
        assert await mw.after_decompose(ctx) is ctx

    async def test_before_dispatch(self) -> None:
        mw = BaseCoordinationMiddleware(name="test")
        ctx = _mw_context()
        assert await mw.before_dispatch(ctx) is ctx

    async def test_after_rollup(self) -> None:
        mw = BaseCoordinationMiddleware(name="test")
        ctx = _mw_context()
        assert await mw.after_rollup(ctx) is ctx

    async def test_before_update_parent(self) -> None:
        mw = BaseCoordinationMiddleware(name="test")
        ctx = _mw_context()
        assert await mw.before_update_parent(ctx) is ctx


# ── Chain composition ─────────────────────────────────────────────


class _TrackingCoordMiddleware(BaseCoordinationMiddleware):
    """Records hook invocations for testing composition order."""

    def __init__(self, *, name: str, log: list[str]) -> None:
        super().__init__(name=name)
        self._log = log

    @override
    async def before_decompose(
        self, ctx: CoordinationMiddlewareContext
    ) -> CoordinationMiddlewareContext:
        self._log.append(f"{self.name}:before_decompose")
        return ctx

    @override
    async def after_decompose(
        self, ctx: CoordinationMiddlewareContext
    ) -> CoordinationMiddlewareContext:
        self._log.append(f"{self.name}:after_decompose")
        return ctx

    @override
    async def before_dispatch(
        self, ctx: CoordinationMiddlewareContext
    ) -> CoordinationMiddlewareContext:
        self._log.append(f"{self.name}:before_dispatch")
        return ctx

    @override
    async def after_rollup(
        self, ctx: CoordinationMiddlewareContext
    ) -> CoordinationMiddlewareContext:
        self._log.append(f"{self.name}:after_rollup")
        return ctx

    @override
    async def before_update_parent(
        self, ctx: CoordinationMiddlewareContext
    ) -> CoordinationMiddlewareContext:
        self._log.append(f"{self.name}:before_update_parent")
        return ctx


@pytest.mark.unit
class TestCoordinationMiddlewareChain:
    """Chain composition: all hooks run left-to-right (linear)."""

    def test_empty_chain(self) -> None:
        chain = CoordinationMiddlewareChain()
        assert len(chain) == 0
        assert chain.names == ()

    def test_names(self) -> None:
        chain = CoordinationMiddlewareChain(
            (
                BaseCoordinationMiddleware(name="a"),
                BaseCoordinationMiddleware(name="b"),
            )
        )
        assert chain.names == ("a", "b")

    async def test_before_decompose_left_to_right(self) -> None:
        log: list[str] = []
        chain = CoordinationMiddlewareChain(
            (
                _TrackingCoordMiddleware(name="a", log=log),
                _TrackingCoordMiddleware(name="b", log=log),
                _TrackingCoordMiddleware(name="c", log=log),
            )
        )
        ctx = _mw_context()
        await chain.run_before_decompose(ctx)
        assert log == [
            "a:before_decompose",
            "b:before_decompose",
            "c:before_decompose",
        ]

    async def test_after_decompose_left_to_right(self) -> None:
        log: list[str] = []
        chain = CoordinationMiddlewareChain(
            (
                _TrackingCoordMiddleware(name="a", log=log),
                _TrackingCoordMiddleware(name="b", log=log),
                _TrackingCoordMiddleware(name="c", log=log),
            )
        )
        ctx = _mw_context()
        await chain.run_after_decompose(ctx)
        assert log == [
            "a:after_decompose",
            "b:after_decompose",
            "c:after_decompose",
        ]

    async def test_before_dispatch_left_to_right(self) -> None:
        log: list[str] = []
        chain = CoordinationMiddlewareChain(
            (
                _TrackingCoordMiddleware(name="a", log=log),
                _TrackingCoordMiddleware(name="b", log=log),
            )
        )
        ctx = _mw_context()
        await chain.run_before_dispatch(ctx)
        assert log == [
            "a:before_dispatch",
            "b:before_dispatch",
        ]

    async def test_after_rollup_left_to_right(self) -> None:
        log: list[str] = []
        chain = CoordinationMiddlewareChain(
            (
                _TrackingCoordMiddleware(name="a", log=log),
                _TrackingCoordMiddleware(name="b", log=log),
            )
        )
        ctx = _mw_context()
        await chain.run_after_rollup(ctx)
        assert log == ["a:after_rollup", "b:after_rollup"]

    async def test_before_update_parent_left_to_right(self) -> None:
        log: list[str] = []
        chain = CoordinationMiddlewareChain(
            (
                _TrackingCoordMiddleware(name="a", log=log),
                _TrackingCoordMiddleware(name="b", log=log),
            )
        )
        ctx = _mw_context()
        await chain.run_before_update_parent(ctx)
        assert log == [
            "a:before_update_parent",
            "b:before_update_parent",
        ]


# ── Error propagation ─────────────────────────────────────────────


class _ErrorCoordMiddleware(BaseCoordinationMiddleware):
    """Raises in before_decompose."""

    @override
    async def before_decompose(
        self, ctx: CoordinationMiddlewareContext
    ) -> CoordinationMiddlewareContext:
        msg = f"Error from {self.name}"
        raise RuntimeError(msg)


@pytest.mark.unit
class TestCoordinationChainErrorPropagation:
    """Exceptions propagate through coordination chain."""

    async def test_error_propagates(self) -> None:
        chain = CoordinationMiddlewareChain((_ErrorCoordMiddleware(name="bad"),))
        ctx = _mw_context()
        with pytest.raises(RuntimeError, match="Error from bad"):
            await chain.run_before_decompose(ctx)

    async def test_error_stops_chain(self) -> None:
        log: list[str] = []
        chain = CoordinationMiddlewareChain(
            (
                _ErrorCoordMiddleware(name="bad"),
                _TrackingCoordMiddleware(name="good", log=log),
            )
        )
        ctx = _mw_context()
        with pytest.raises(RuntimeError):
            await chain.run_before_decompose(ctx)
        assert log == []


# ── Context metadata ──────────────────────────────────────────────


@pytest.mark.unit
class TestCoordinationMiddlewareContext:
    """CoordinationMiddlewareContext model behavior."""

    def test_frozen(self) -> None:
        ctx = _mw_context()
        with pytest.raises(ValidationError):
            ctx.decomposition_result = "bad"  # type: ignore[misc,assignment]

    def test_with_metadata(self) -> None:
        ctx = _mw_context()
        updated = ctx.with_metadata("key", "value")
        assert updated.metadata["key"] == "value"
        assert "key" not in ctx.metadata

    def test_metadata_defensive_copy(self) -> None:
        """Modifying the input dict does not affect the frozen context."""
        input_dict = {"key": "value"}
        ctx = CoordinationMiddlewareContext(
            coordination_context=_coord_context(),
            metadata=input_dict,
        )
        # Mutate the original dict
        input_dict["key"] = "mutated"
        # Context should be unaffected
        assert ctx.metadata["key"] == "value"

    def test_metadata_not_shared_between_copies(self) -> None:
        """Metadata from with_metadata does not leak to the original."""
        ctx = _mw_context()
        updated = ctx.with_metadata("a", [1, 2, 3])
        # Mutate the list in the updated context
        updated.metadata["a"].append(4)
        # Original should not have "a" at all
        assert "a" not in ctx.metadata

    def test_default_fields_none(self) -> None:
        ctx = _mw_context()
        assert ctx.decomposition_result is None
        assert ctx.routing_result is None
        assert ctx.dispatch_result is None
        assert ctx.status_rollup is None
        assert ctx.task_ledger is None
        assert ctx.progress_ledger is None
        assert ctx.phases == ()
