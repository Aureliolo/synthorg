"""Tests for memory injection protocol, enums, and token estimator."""

import pytest

from synthorg.memory.injection import (
    DefaultTokenEstimator,
    InjectionPoint,
    InjectionStrategy,
    MemoryInjectionStrategy,
    TokenEstimator,
)

pytestmark = pytest.mark.timeout(30)


# ── InjectionStrategy enum ──────────────────────────────────────


@pytest.mark.unit
class TestInjectionStrategy:
    def test_values(self) -> None:
        assert InjectionStrategy.CONTEXT.value == "context"
        assert InjectionStrategy.TOOL_BASED.value == "tool_based"
        assert InjectionStrategy.SELF_EDITING.value == "self_editing"

    def test_member_count(self) -> None:
        assert len(InjectionStrategy) == 3

    def test_from_string(self) -> None:
        assert InjectionStrategy("context") is InjectionStrategy.CONTEXT
        assert InjectionStrategy("tool_based") is InjectionStrategy.TOOL_BASED
        assert InjectionStrategy("self_editing") is InjectionStrategy.SELF_EDITING

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError, match="not_a_strategy"):
            InjectionStrategy("not_a_strategy")


# ── InjectionPoint enum ─────────────────────────────────────────


@pytest.mark.unit
class TestInjectionPoint:
    def test_values(self) -> None:
        assert InjectionPoint.SYSTEM.value == "system"
        assert InjectionPoint.USER.value == "user"

    def test_member_count(self) -> None:
        assert len(InjectionPoint) == 2


# ── TokenEstimator protocol ─────────────────────────────────────


@pytest.mark.unit
class TestTokenEstimator:
    def test_default_estimator_satisfies_protocol(self) -> None:
        estimator = DefaultTokenEstimator()
        assert isinstance(estimator, TokenEstimator)

    def test_custom_estimator_satisfies_protocol(self) -> None:
        class CustomEstimator:
            def estimate_tokens(self, text: str) -> int:
                return len(text)

        assert isinstance(CustomEstimator(), TokenEstimator)


# ── DefaultTokenEstimator ───────────────────────────────────────


@pytest.mark.unit
class TestDefaultTokenEstimator:
    def test_empty_string(self) -> None:
        est = DefaultTokenEstimator()
        assert est.estimate_tokens("") == 0

    def test_short_string(self) -> None:
        est = DefaultTokenEstimator()
        assert est.estimate_tokens("abc") == 1  # min 1 for non-empty

    def test_four_chars(self) -> None:
        est = DefaultTokenEstimator()
        assert est.estimate_tokens("abcd") == 1

    def test_longer_string(self) -> None:
        est = DefaultTokenEstimator()
        text = "Hello, world! This is a test."
        assert est.estimate_tokens(text) == len(text) // 4

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("a" * 100, 25),
            ("a" * 101, 25),
            ("a" * 400, 100),
        ],
    )
    def test_various_lengths(self, text: str, expected: int) -> None:
        est = DefaultTokenEstimator()
        assert est.estimate_tokens(text) == expected


# ── MemoryInjectionStrategy protocol ────────────────────────────


@pytest.mark.unit
class TestMemoryInjectionStrategy:
    def test_is_runtime_checkable(self) -> None:
        """Protocol is runtime_checkable for isinstance checks."""

        class FakeStrategy:
            async def prepare_messages(
                self,
                agent_id: str,
                query_text: str,
                token_budget: int,
            ) -> tuple[object, ...]:
                return ()

            def get_tool_definitions(self) -> tuple[object, ...]:
                return ()

            @property
            def strategy_name(self) -> str:
                return "fake"

        assert isinstance(FakeStrategy(), MemoryInjectionStrategy)

    def test_missing_method_not_instance(self) -> None:
        """Objects missing required methods are not instances."""

        class Incomplete:
            async def prepare_messages(
                self, agent_id: str, query_text: str, token_budget: int
            ) -> tuple[object, ...]:
                return ()

        assert not isinstance(Incomplete(), MemoryInjectionStrategy)
