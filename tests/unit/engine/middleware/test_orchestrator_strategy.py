"""Tests for orchestrator strategy protocol and implementations."""

import pytest

from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.engine.middleware.models import ProgressLedger
from synthorg.engine.middleware.orchestrator_strategy import (
    MagenticDynamicSelectStrategy,
    NaiveDispatchStrategy,
    OrchestratorStrategy,
    create_orchestrator_strategy,
)

# ── Protocol compliance ───────────────────────────────────────────


@pytest.mark.unit
class TestOrchestratorStrategyProtocol:
    """OrchestratorStrategy protocol compliance."""

    def test_naive_satisfies_protocol(self) -> None:
        s = NaiveDispatchStrategy()
        assert isinstance(s, OrchestratorStrategy)

    def test_magentic_satisfies_protocol(self) -> None:
        s = MagenticDynamicSelectStrategy()
        assert isinstance(s, OrchestratorStrategy)


# ── NaiveDispatchStrategy ─────────────────────────────────────────


@pytest.mark.unit
class TestNaiveDispatchStrategy:
    """NaiveDispatchStrategy returns all subtasks in order."""

    def test_name(self) -> None:
        assert NaiveDispatchStrategy().name == "naive"

    async def test_returns_all_subtasks(self) -> None:
        s = NaiveDispatchStrategy()
        ids = ("st-1", "st-2", "st-3")
        result = await s.select_subtasks(ids, None)
        assert result == ids

    async def test_preserves_order(self) -> None:
        s = NaiveDispatchStrategy()
        ids = ("c", "a", "b")
        result = await s.select_subtasks(ids, None)
        assert result == ("c", "a", "b")

    async def test_ignores_progress(self) -> None:
        s = NaiveDispatchStrategy()
        progress = ProgressLedger(
            round_number=1,
            progress_made=True,
            next_action="continue",
        )
        ids = ("st-1",)
        result = await s.select_subtasks(ids, progress)
        assert result == ("st-1",)


# ── MagenticDynamicSelectStrategy ─────────────────────────────────


@pytest.mark.unit
class TestMagenticDynamicSelectStrategy:
    """MagenticDynamicSelectStrategy prioritizes blocked subtasks."""

    def test_name(self) -> None:
        assert MagenticDynamicSelectStrategy().name == "magentic_dynamic"

    async def test_no_progress_returns_all(self) -> None:
        s = MagenticDynamicSelectStrategy()
        ids = ("st-1", "st-2", "st-3")
        result = await s.select_subtasks(ids, None)
        assert result == ids

    async def test_no_blocking_returns_all(self) -> None:
        s = MagenticDynamicSelectStrategy()
        progress = ProgressLedger(
            round_number=1,
            progress_made=True,
            next_action="continue",
        )
        ids = ("st-1", "st-2")
        result = await s.select_subtasks(ids, progress)
        assert result == ids

    async def test_prioritizes_blocked_subtask(self) -> None:
        s = MagenticDynamicSelectStrategy()
        progress = ProgressLedger(
            round_number=2,
            progress_made=False,
            stall_count=1,
            blocking_issues=("Phase dispatch: st-2 failed",),
            next_action="replan",
        )
        ids = ("st-1", "st-2", "st-3")
        result = await s.select_subtasks(ids, progress)
        # st-2 should be first (mentioned in blocking issue)
        assert result[0] == "st-2"
        # All IDs present
        assert set(result) == set(ids)

    async def test_preserves_remaining_order(self) -> None:
        s = MagenticDynamicSelectStrategy()
        progress = ProgressLedger(
            round_number=2,
            progress_made=False,
            stall_count=1,
            blocking_issues=("st-3 is blocked",),
            next_action="replan",
        )
        ids = ("st-1", "st-2", "st-3")
        result = await s.select_subtasks(ids, progress)
        # st-3 first, then st-1, st-2 in original order
        assert result == ("st-3", "st-1", "st-2")


# ── create_orchestrator_strategy factory ──────────────────────────


@pytest.mark.unit
class TestCreateOrchestratorStrategy:
    """create_orchestrator_strategy maps a discriminator to a strategy."""

    def test_naive_is_safe_default(self) -> None:
        strategy = create_orchestrator_strategy("naive")
        assert isinstance(strategy, NaiveDispatchStrategy)
        assert strategy.name == "naive"

    def test_magentic_dynamic(self) -> None:
        strategy = create_orchestrator_strategy("magentic_dynamic")
        assert isinstance(strategy, MagenticDynamicSelectStrategy)
        assert strategy.name == "magentic_dynamic"

    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(StrategyFactoryNotFoundError):
            create_orchestrator_strategy("bogus")
