# mypy: disable-error-code="explicit-any"
"""Tests for procedural memory domain models."""

from typing import Any

import pytest
from pydantic import ValidationError

from synthorg.core.enums import TaskType
from synthorg.memory.procedural.models import (
    FailureAnalysisPayload,
    ProceduralMemoryConfig,
    ProceduralMemoryProposal,
)


def _make_payload(**overrides: Any) -> FailureAnalysisPayload:
    """Build a valid FailureAnalysisPayload with overridable defaults."""
    defaults: dict[str, Any] = {
        "task_id": "task-001",
        "task_title": "Implement auth module",
        "task_description": "Create JWT authentication.",
        "task_type": TaskType.DEVELOPMENT,
        "error_message": "LLM timeout after 30s",
        "strategy_type": "fail_reassign",
        "termination_reason": "error",
        "turn_count": 5,
        "tool_calls_made": ("code_search", "run_tests"),
        "retry_count": 0,
        "max_retries": 2,
        "can_reassign": True,
    }
    defaults.update(overrides)
    return FailureAnalysisPayload(**defaults)


def _make_proposal(**overrides: Any) -> ProceduralMemoryProposal:
    """Build a valid ProceduralMemoryProposal with overridable defaults."""
    defaults: dict[str, Any] = {
        "discovery": "When facing LLM timeouts, break task into smaller steps.",
        "condition": "Task exceeds 10 turns without progress.",
        "action": "Decompose the task into subtasks before retrying.",
        "rationale": "Smaller tasks reduce context window pressure.",
        "confidence": 0.85,
        "tags": ("timeout", "decomposition"),
    }
    defaults.update(overrides)
    return ProceduralMemoryProposal(**defaults)


# -- FailureAnalysisPayload -------------------------------------------


@pytest.mark.unit
class TestFailureAnalysisPayload:
    def test_happy_path(self) -> None:
        p = _make_payload()
        assert p.task_id == "task-001"
        assert p.task_type is TaskType.DEVELOPMENT
        assert p.turn_count == 5
        assert p.tool_calls_made == ("code_search", "run_tests")
        assert p.can_reassign is True

    def test_frozen(self) -> None:
        p = _make_payload()
        with pytest.raises(ValidationError):
            p.task_id = "other"  # type: ignore[misc]

    def test_empty_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_payload(task_id="")

    def test_whitespace_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_payload(task_id="   ")

    def test_negative_turn_count_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_payload(turn_count=-1)

    def test_zero_turn_count_accepted(self) -> None:
        p = _make_payload(turn_count=0)
        assert p.turn_count == 0

    def test_negative_retry_count_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_payload(retry_count=-1)

    def test_negative_max_retries_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_payload(max_retries=-1)

    def test_empty_tool_calls_accepted(self) -> None:
        p = _make_payload(tool_calls_made=())
        assert p.tool_calls_made == ()

    def test_nan_turn_count_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_payload(turn_count=float("nan"))

    def test_retry_count_exceeds_max_retries_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"retry_count.*exceeds"):
            _make_payload(retry_count=3, max_retries=2)

    def test_retry_count_equals_max_retries_accepted(self) -> None:
        p = _make_payload(retry_count=2, max_retries=2)
        assert p.retry_count == 2

    def test_error_category_default(self) -> None:
        p = _make_payload()
        assert p.error_category == "unknown"

    def test_error_category_custom(self) -> None:
        p = _make_payload(error_category="provider")
        assert p.error_category == "provider"

    def test_error_category_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_payload(error_category="")

    def test_error_category_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_payload(error_category="   ")

    def test_missing_capability_default_none(self) -> None:
        p = _make_payload()
        assert p.missing_capability is None

    def test_missing_capability_set(self) -> None:
        p = _make_payload(missing_capability="code_review")
        assert p.missing_capability == "code_review"

    def test_missing_capability_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_payload(missing_capability="")

    def test_missing_capability_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_payload(missing_capability="   ")


# -- ProceduralMemoryProposal -----------------------------------------


@pytest.mark.unit
class TestProceduralMemoryProposal:
    def test_happy_path(self) -> None:
        p = _make_proposal()
        assert p.discovery.startswith("When facing")
        assert p.confidence == 0.85
        assert p.tags == ("timeout", "decomposition")

    def test_frozen(self) -> None:
        p = _make_proposal()
        with pytest.raises(ValidationError):
            p.confidence = 0.5  # type: ignore[misc]

    def test_empty_discovery_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_proposal(discovery="")

    def test_whitespace_condition_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_proposal(condition="   ")

    def test_confidence_lower_bound(self) -> None:
        p = _make_proposal(confidence=0.0)
        assert p.confidence == 0.0

    def test_confidence_upper_bound(self) -> None:
        p = _make_proposal(confidence=1.0)
        assert p.confidence == 1.0

    def test_confidence_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_proposal(confidence=-0.1)

    def test_confidence_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_proposal(confidence=1.1)

    def test_confidence_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_proposal(confidence=float("nan"))

    def test_default_tags_empty(self) -> None:
        p = _make_proposal(tags=())
        assert p.tags == ()

    def test_duplicate_tags_deduplicated(self) -> None:
        p = _make_proposal(tags=("api", "api", "timeout"))
        assert p.tags == ("api", "timeout")

    def test_execution_steps_default_empty(self) -> None:
        p = _make_proposal()
        assert p.execution_steps == ()

    def test_execution_steps_preserved(self) -> None:
        steps = ("Check logs", "Restart service")
        p = _make_proposal(execution_steps=steps)
        assert p.execution_steps == steps

    def test_discovery_max_length_enforced(self) -> None:
        with pytest.raises(ValidationError):
            _make_proposal(discovery="x" * 601)

    def test_execution_steps_max_50(self) -> None:
        steps = tuple(f"Step {i}" for i in range(51))
        with pytest.raises(ValidationError):
            _make_proposal(execution_steps=steps)

    def test_execution_steps_at_50_accepted(self) -> None:
        steps = tuple(f"Step {i}" for i in range(50))
        p = _make_proposal(execution_steps=steps)
        assert len(p.execution_steps) == 50

    def test_tags_capped_at_20(self) -> None:
        tags = tuple(f"tag-{i}" for i in range(25))
        p = _make_proposal(tags=tags)
        assert len(p.tags) == 20

    def test_tags_dedup_before_cap(self) -> None:
        """Duplicates are removed before the 20-tag cap is applied."""
        tags = tuple(f"tag-{i % 5}" for i in range(25))
        p = _make_proposal(tags=tags)
        assert len(p.tags) == 5


# -- ProceduralMemoryConfig --------------------------------------------


@pytest.mark.unit
class TestProceduralMemoryConfig:
    def test_defaults(self) -> None:
        c = ProceduralMemoryConfig()
        assert c.enabled is True
        assert c.model == "example-small-001"
        assert c.temperature == 0.3
        assert c.max_tokens == 1500
        assert c.min_confidence == 0.5
        assert c.skill_md_directory is None

    def test_frozen(self) -> None:
        c = ProceduralMemoryConfig()
        with pytest.raises(ValidationError):
            c.enabled = False  # type: ignore[misc]

    def test_custom_model(self) -> None:
        c = ProceduralMemoryConfig(model="test-provider/test-large-001")
        assert c.model == "test-provider/test-large-001"

    def test_empty_model_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProceduralMemoryConfig(model="")

    def test_temperature_bounds(self) -> None:
        assert ProceduralMemoryConfig(temperature=0.0).temperature == 0.0
        assert ProceduralMemoryConfig(temperature=2.0).temperature == 2.0

    def test_temperature_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProceduralMemoryConfig(temperature=-0.1)

    def test_temperature_above_two_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProceduralMemoryConfig(temperature=2.1)

    def test_max_tokens_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProceduralMemoryConfig(max_tokens=0)

    def test_min_confidence_bounds(self) -> None:
        assert ProceduralMemoryConfig(min_confidence=0.0).min_confidence == 0.0
        assert ProceduralMemoryConfig(min_confidence=1.0).min_confidence == 1.0

    def test_min_confidence_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProceduralMemoryConfig(min_confidence=1.1)

    def test_disabled(self) -> None:
        c = ProceduralMemoryConfig(enabled=False)
        assert c.enabled is False

    def test_skill_md_directory(self) -> None:
        c = ProceduralMemoryConfig(skill_md_directory="/var/data/skills")
        assert c.skill_md_directory == "/var/data/skills"

    def test_skill_md_directory_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProceduralMemoryConfig(skill_md_directory="")

    def test_skill_md_directory_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProceduralMemoryConfig(skill_md_directory="   ")
