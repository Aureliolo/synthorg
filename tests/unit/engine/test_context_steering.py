"""Tests for AgentContext mid-flight steering adoption state.

Adoption is context-local (consume-once per execution), so it must survive the
model round-trip and old checkpoints (without the field) must still deserialise.
A checkpoint written before the field existed, and one written while the retired
pending-replan field still did, both have to load.
"""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext


@pytest.mark.unit
class TestSteeringAdoption:
    """``with_steering_adopted`` records consume-once adoption ids."""

    def test_defaults_are_empty(self, sample_agent_context: AgentContext) -> None:
        assert sample_agent_context.adopted_steering_ids == frozenset()

    def test_records_id_without_mutating_original(
        self, sample_agent_context: AgentContext
    ) -> None:
        updated = sample_agent_context.with_steering_adopted(NotBlankStr("dir-1"))
        assert "dir-1" in updated.adopted_steering_ids
        assert sample_agent_context.adopted_steering_ids == frozenset()

    def test_is_idempotent(self, sample_agent_context: AgentContext) -> None:
        once = sample_agent_context.with_steering_adopted(NotBlankStr("dir-1"))
        twice = once.with_steering_adopted(NotBlankStr("dir-1"))
        assert twice is once

    def test_accumulates_distinct_ids(self, sample_agent_context: AgentContext) -> None:
        ctx = sample_agent_context.with_steering_adopted(
            NotBlankStr("dir-1")
        ).with_steering_adopted(NotBlankStr("dir-2"))
        assert ctx.adopted_steering_ids == frozenset({"dir-1", "dir-2"})


@pytest.mark.unit
class TestSteeringCheckpointRoundTrip:
    """Checkpoint JSON must preserve adoption and tolerate absent fields."""

    def test_roundtrip_preserves_fields(
        self, sample_agent_context: AgentContext
    ) -> None:
        ctx = sample_agent_context.with_steering_adopted(NotBlankStr("dir-1"))
        restored = AgentContext.model_validate_json(ctx.model_dump_json())
        assert restored.adopted_steering_ids == frozenset({"dir-1"})

    def test_old_checkpoint_without_fields_deserialises(
        self, sample_agent_context: AgentContext
    ) -> None:
        data = sample_agent_context.model_dump(mode="json")
        data.pop("adopted_steering_ids", None)
        restored = AgentContext.model_validate(data)
        assert restored.adopted_steering_ids == frozenset()

    def test_checkpoint_carrying_the_retired_replan_field_deserialises(
        self, sample_agent_context: AgentContext
    ) -> None:
        data = sample_agent_context.model_dump(mode="json")
        data["pending_steering_replan_id"] = "dir-1"
        restored = AgentContext.model_validate(data)
        assert restored.adopted_steering_ids == frozenset()
