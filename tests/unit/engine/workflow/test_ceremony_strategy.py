"""Tests for CeremonySchedulingStrategy protocol conformance."""

from collections.abc import Mapping

import pytest

from synthorg.engine.workflow.ceremony_context import CeremonyEvalContext
from synthorg.engine.workflow.ceremony_policy import CeremonyStrategyType
from synthorg.engine.workflow.ceremony_strategy import (
    CeremonySchedulingStrategy,
)
from synthorg.engine.workflow.sprint_config import (
    SprintCeremonyConfig,
    SprintConfig,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.velocity_types import VelocityCalcType


class _StubStrategy:
    """Minimal stub implementing all protocol methods."""

    def should_fire_ceremony(
        self,
        ceremony: SprintCeremonyConfig,
        sprint: Sprint,
        context: CeremonyEvalContext,
    ) -> bool:
        return False

    def should_transition_sprint(
        self,
        sprint: Sprint,
        config: SprintConfig,
        context: CeremonyEvalContext,
    ) -> SprintStatus | None:
        return None

    async def on_sprint_activated(
        self,
        sprint: Sprint,
        config: SprintConfig,
    ) -> None:
        pass

    async def on_sprint_deactivated(self) -> None:
        pass

    async def on_task_completed(
        self,
        sprint: Sprint,
        task_id: str,
        story_points: float,
        context: CeremonyEvalContext,
    ) -> None:
        pass

    async def on_task_added(
        self,
        sprint: Sprint,
        task_id: str,
    ) -> None:
        pass

    async def on_task_blocked(
        self,
        sprint: Sprint,
        task_id: str,
    ) -> None:
        pass

    async def on_budget_updated(
        self,
        sprint: Sprint,
        budget_consumed_fraction: float,
    ) -> None:
        pass

    async def on_external_event(
        self,
        sprint: Sprint,
        event_name: str,
        payload: Mapping[str, object],
    ) -> None:
        pass

    @property
    def strategy_type(self) -> CeremonyStrategyType:
        return CeremonyStrategyType.TASK_DRIVEN

    def get_default_velocity_calculator(self) -> VelocityCalcType:
        return VelocityCalcType.TASK_DRIVEN

    def validate_strategy_config(
        self,
        config: Mapping[str, object],
    ) -> None:
        pass


class TestCeremonySchedulingStrategyProtocol:
    """Protocol conformance tests."""

    @pytest.mark.unit
    def test_stub_is_instance(self) -> None:
        stub = _StubStrategy()
        assert isinstance(stub, CeremonySchedulingStrategy)

    @pytest.mark.unit
    def test_protocol_is_runtime_checkable(self) -> None:
        assert hasattr(CeremonySchedulingStrategy, "__protocol_attrs__")

    @pytest.mark.unit
    def test_stub_should_fire_returns_bool(self) -> None:
        stub = _StubStrategy()
        result = stub.should_fire_ceremony(
            ceremony=None,  # type: ignore[arg-type]
            sprint=None,  # type: ignore[arg-type]
            context=None,  # type: ignore[arg-type]
        )
        assert result is False

    @pytest.mark.unit
    def test_stub_should_transition_returns_none(self) -> None:
        stub = _StubStrategy()
        result = stub.should_transition_sprint(
            sprint=None,  # type: ignore[arg-type]
            config=None,  # type: ignore[arg-type]
            context=None,  # type: ignore[arg-type]
        )
        assert result is None

    @pytest.mark.unit
    def test_stub_strategy_type(self) -> None:
        stub = _StubStrategy()
        assert stub.strategy_type is CeremonyStrategyType.TASK_DRIVEN

    @pytest.mark.unit
    def test_stub_default_velocity_calculator(self) -> None:
        stub = _StubStrategy()
        assert stub.get_default_velocity_calculator() is VelocityCalcType.TASK_DRIVEN
