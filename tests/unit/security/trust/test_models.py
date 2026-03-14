"""Tests for trust domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.security.trust.enums import TrustChangeReason
from synthorg.security.trust.models import (
    TrustChangeRecord,
    TrustEvaluationResult,
    TrustState,
)

pytestmark = pytest.mark.timeout(30)

_NOW = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)


# ── TrustState ───────────────────────────────────────────────────


@pytest.mark.unit
class TestTrustState:
    """Tests for TrustState creation and immutability."""

    def test_create_with_defaults(self) -> None:
        state = TrustState(agent_id=NotBlankStr("agent-001"))

        assert state.agent_id == "agent-001"
        assert state.global_level == ToolAccessLevel.SANDBOXED
        assert state.category_levels == {}
        assert state.trust_score is None
        assert state.last_evaluated_at is None
        assert state.last_promoted_at is None
        assert state.last_decay_check_at is None
        assert state.milestone_progress == {}

    def test_create_with_explicit_level(self) -> None:
        state = TrustState(
            agent_id=NotBlankStr("agent-002"),
            global_level=ToolAccessLevel.STANDARD,
        )
        assert state.global_level == ToolAccessLevel.STANDARD

    def test_create_with_trust_score(self) -> None:
        state = TrustState(
            agent_id=NotBlankStr("agent-003"),
            trust_score=0.75,
        )
        assert state.trust_score == 0.75

    def test_frozen(self) -> None:
        state = TrustState(agent_id=NotBlankStr("agent-001"))
        with pytest.raises(ValidationError):
            state.global_level = ToolAccessLevel.ELEVATED  # type: ignore[misc]

    def test_trust_score_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            TrustState(
                agent_id=NotBlankStr("agent-001"),
                trust_score=-0.1,
            )

    def test_trust_score_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            TrustState(
                agent_id=NotBlankStr("agent-001"),
                trust_score=1.1,
            )

    def test_model_copy_update(self) -> None:
        state = TrustState(agent_id=NotBlankStr("agent-001"))
        updated = state.model_copy(
            update={"global_level": ToolAccessLevel.RESTRICTED},
        )
        assert updated.global_level == ToolAccessLevel.RESTRICTED
        assert state.global_level == ToolAccessLevel.SANDBOXED


# ── TrustChangeRecord ───────────────────────────────────────────


@pytest.mark.unit
class TestTrustChangeRecord:
    """Tests for TrustChangeRecord creation and defaults."""

    def test_create_with_required_fields(self) -> None:
        record = TrustChangeRecord(
            agent_id=NotBlankStr("agent-001"),
            old_level=ToolAccessLevel.SANDBOXED,
            new_level=ToolAccessLevel.RESTRICTED,
            reason=TrustChangeReason.SCORE_THRESHOLD,
            timestamp=_NOW,
        )
        assert record.agent_id == "agent-001"
        assert record.old_level == ToolAccessLevel.SANDBOXED
        assert record.new_level == ToolAccessLevel.RESTRICTED
        assert record.reason == TrustChangeReason.SCORE_THRESHOLD
        assert record.timestamp == _NOW

    def test_default_id_generated(self) -> None:
        record = TrustChangeRecord(
            agent_id=NotBlankStr("agent-001"),
            old_level=ToolAccessLevel.SANDBOXED,
            new_level=ToolAccessLevel.RESTRICTED,
            reason=TrustChangeReason.SCORE_THRESHOLD,
            timestamp=_NOW,
        )
        assert record.id is not None
        assert len(record.id) > 0

    def test_two_records_have_distinct_ids(self) -> None:
        r1 = TrustChangeRecord(
            agent_id=NotBlankStr("agent-001"),
            old_level=ToolAccessLevel.SANDBOXED,
            new_level=ToolAccessLevel.RESTRICTED,
            reason=TrustChangeReason.SCORE_THRESHOLD,
            timestamp=_NOW,
        )
        r2 = TrustChangeRecord(
            agent_id=NotBlankStr("agent-001"),
            old_level=ToolAccessLevel.SANDBOXED,
            new_level=ToolAccessLevel.RESTRICTED,
            reason=TrustChangeReason.SCORE_THRESHOLD,
            timestamp=_NOW,
        )
        assert r1.id != r2.id

    def test_default_optional_fields(self) -> None:
        record = TrustChangeRecord(
            agent_id=NotBlankStr("agent-001"),
            old_level=ToolAccessLevel.SANDBOXED,
            new_level=ToolAccessLevel.RESTRICTED,
            reason=TrustChangeReason.MANUAL,
            timestamp=_NOW,
        )
        assert record.category is None
        assert record.approval_id is None
        assert record.details == ""

    def test_frozen(self) -> None:
        record = TrustChangeRecord(
            agent_id=NotBlankStr("agent-001"),
            old_level=ToolAccessLevel.SANDBOXED,
            new_level=ToolAccessLevel.RESTRICTED,
            reason=TrustChangeReason.MANUAL,
            timestamp=_NOW,
        )
        with pytest.raises(ValidationError):
            record.reason = TrustChangeReason.TRUST_DECAY  # type: ignore[misc]


# ── TrustEvaluationResult ───────────────────────────────────────


@pytest.mark.unit
class TestTrustEvaluationResult:
    """Tests for TrustEvaluationResult, including computed should_change."""

    def test_create_minimal(self) -> None:
        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.RESTRICTED,
            current_level=ToolAccessLevel.SANDBOXED,
            strategy_name=NotBlankStr("weighted"),
        )
        assert result.agent_id == "agent-001"
        assert result.recommended_level == ToolAccessLevel.RESTRICTED
        assert result.current_level == ToolAccessLevel.SANDBOXED
        assert result.requires_human_approval is False
        assert result.score is None
        assert result.details == ""
        assert result.strategy_name == "weighted"

    @pytest.mark.parametrize(
        ("recommended", "current", "expected"),
        [
            (ToolAccessLevel.RESTRICTED, ToolAccessLevel.SANDBOXED, True),
            (ToolAccessLevel.STANDARD, ToolAccessLevel.RESTRICTED, True),
            (ToolAccessLevel.ELEVATED, ToolAccessLevel.STANDARD, True),
            (ToolAccessLevel.SANDBOXED, ToolAccessLevel.RESTRICTED, True),
        ],
        ids=[
            "promote-sandboxed-to-restricted",
            "promote-restricted-to-standard",
            "promote-standard-to-elevated",
            "demote-restricted-to-sandboxed",
        ],
    )
    def test_should_change_true(
        self,
        recommended: ToolAccessLevel,
        current: ToolAccessLevel,
        expected: bool,
    ) -> None:
        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=recommended,
            current_level=current,
            strategy_name=NotBlankStr("test"),
        )
        assert result.should_change is expected

    @pytest.mark.parametrize(
        "level",
        [
            ToolAccessLevel.SANDBOXED,
            ToolAccessLevel.RESTRICTED,
            ToolAccessLevel.STANDARD,
            ToolAccessLevel.ELEVATED,
        ],
        ids=["sandboxed", "restricted", "standard", "elevated"],
    )
    def test_should_change_false_when_same(
        self,
        level: ToolAccessLevel,
    ) -> None:
        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=level,
            current_level=level,
            strategy_name=NotBlankStr("test"),
        )
        assert result.should_change is False

    def test_frozen(self) -> None:
        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.SANDBOXED,
            current_level=ToolAccessLevel.SANDBOXED,
            strategy_name=NotBlankStr("test"),
        )
        with pytest.raises(ValidationError):
            result.score = 0.5  # type: ignore[misc]

    def test_score_bounds(self) -> None:
        with pytest.raises(ValidationError):
            TrustEvaluationResult(
                agent_id=NotBlankStr("agent-001"),
                recommended_level=ToolAccessLevel.SANDBOXED,
                current_level=ToolAccessLevel.SANDBOXED,
                strategy_name=NotBlankStr("test"),
                score=1.5,
            )

    def test_with_score(self) -> None:
        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.RESTRICTED,
            current_level=ToolAccessLevel.SANDBOXED,
            score=0.75,
            strategy_name=NotBlankStr("weighted"),
        )
        assert result.score == 0.75
        assert result.should_change is True
