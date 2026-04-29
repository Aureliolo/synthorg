"""Tests for the typed WebSocket event payload union."""

from typing import get_args

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from synthorg.api.ws_models import WsEventType
from synthorg.api.ws_payloads import (
    WsAgentCreatedPayload,
    WsAgentDeletedPayload,
    WsApprovalExpiredPayload,
    WsArtifactCreatedPayload,
    WsCoordinationStartedPayload,
    WsEventPayload,
    WsMemoryFineTuneProgressPayload,
    WsMessageSentPayload,
    WsPersonalityTrimmedPayload,
    WsRequestSubmittedPayload,
    WsSimulationStartedPayload,
    WsTaskCreatedPayload,
)

_ADAPTER: TypeAdapter[WsEventPayload] = TypeAdapter(WsEventPayload)


def _union_variants() -> tuple[type[BaseModel], ...]:
    """Return every variant model in the discriminated union."""
    union_alias, _discriminator = get_args(WsEventPayload)
    return get_args(union_alias)


class TestUnionParity:
    """The union must cover :class:`WsEventType` exactly."""

    @pytest.mark.unit
    def test_every_enum_value_has_a_variant(self) -> None:
        """Every WsEventType value maps to exactly one variant model."""
        union_event_types = {
            v.model_fields["event_type"].default for v in _union_variants()
        }
        assert union_event_types == set(WsEventType)

    @pytest.mark.unit
    def test_no_orphan_variants(self) -> None:
        """No variant carries an event_type outside :class:`WsEventType`."""
        for variant in _union_variants():
            default = variant.model_fields["event_type"].default
            assert isinstance(default, WsEventType), (
                f"{variant.__name__} default {default!r} not a WsEventType"
            )

    @pytest.mark.unit
    def test_one_variant_per_enum_value(self) -> None:
        """No two variants share the same event_type discriminator."""
        seen: set[WsEventType] = set()
        for variant in _union_variants():
            default = variant.model_fields["event_type"].default
            assert default not in seen, f"duplicate variant for {default}"
            seen.add(default)


class TestModelInvariants:
    """Every payload model is frozen, forbids extras, and is round-trippable."""

    @pytest.mark.unit
    def test_every_variant_is_frozen(self) -> None:
        """No payload model is mutable after construction."""
        for variant in _union_variants():
            cfg = variant.model_config
            assert cfg.get("frozen") is True, f"{variant.__name__} is not frozen"

    @pytest.mark.unit
    def test_every_variant_forbids_extra_keys(self) -> None:
        """No payload model accepts unknown keys."""
        for variant in _union_variants():
            cfg = variant.model_config
            assert cfg.get("extra") == "forbid", (
                f"{variant.__name__} does not forbid extra keys"
            )

    @pytest.mark.unit
    def test_every_variant_rejects_inf_nan(self) -> None:
        """No payload model accepts NaN/Inf in numeric fields."""
        for variant in _union_variants():
            cfg = variant.model_config
            assert cfg.get("allow_inf_nan") is False, (
                f"{variant.__name__} does not reject NaN/Inf"
            )


class TestDiscriminatorRouting:
    """``TypeAdapter`` routes payloads to the correct variant via ``event_type``."""

    @pytest.mark.unit
    def test_task_created_routes_to_task_created_variant(self) -> None:
        """A task.created payload deserialises into WsTaskCreatedPayload."""
        result = _ADAPTER.validate_python(
            {
                "event_type": "task.created",
                "task_id": "t1",
                "title": "Hello",
                "status": "pending",
            },
        )
        assert isinstance(result, WsTaskCreatedPayload)
        assert result.task_id == "t1"

    @pytest.mark.unit
    def test_message_sent_routes_to_message_variant(self) -> None:
        """A message.sent payload routes to WsMessageSentPayload."""
        result = _ADAPTER.validate_python(
            {
                "event_type": "message.sent",
                "message_id": "m1",
                "sender": "a1",
                "to": "a2",
                "content": "hi",
                "parts": [],
            },
        )
        assert isinstance(result, WsMessageSentPayload)
        assert result.parts == ()

    @pytest.mark.unit
    def test_simulation_started_routes_to_simulation_variant(self) -> None:
        """A simulation.started payload routes correctly."""
        result = _ADAPTER.validate_python(
            {
                "event_type": "simulation.started",
                "simulation_id": "s1",
                "status": "running",
                "progress": 0.0,
            },
        )
        assert isinstance(result, WsSimulationStartedPayload)

    @pytest.mark.unit
    def test_unknown_event_type_rejected(self) -> None:
        """An event_type not in the enum raises ValidationError."""
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(
                {"event_type": "task.exploded", "task_id": "x"},
            )


class TestRoundTrip:
    """Models survive ``model_dump`` / ``model_validate`` cleanly."""

    @pytest.mark.unit
    def test_agent_created_round_trip(self) -> None:
        """Agent created payload survives a JSON round-trip."""
        original = WsAgentCreatedPayload(
            name="alice",
            role="senior_engineer",
            department="engineering",
        )
        restored = _ADAPTER.validate_python(original.model_dump())
        assert restored == original

    @pytest.mark.unit
    def test_personality_trimmed_round_trip(self) -> None:
        """Personality trim payload survives a round-trip."""
        original = WsPersonalityTrimmedPayload(
            agent_id="a1",
            agent_name="Alice",
            task_id="t1",
            trim_tier="aggressive",
            before_tokens=1000,
            after_tokens=500,
        )
        restored = _ADAPTER.validate_python(original.model_dump())
        assert restored == original

    @pytest.mark.unit
    def test_coordination_started_round_trip(self) -> None:
        """Coordination started payload survives a round-trip."""
        original = WsCoordinationStartedPayload(
            task_id="task-1",
            agent_count=3,
        )
        restored = _ADAPTER.validate_python(original.model_dump())
        assert restored == original

    @pytest.mark.unit
    def test_artifact_created_round_trip(self) -> None:
        """Artifact created payload survives a round-trip."""
        original = WsArtifactCreatedPayload(
            artifact_id="art-1",
            task_id="task-1",
            created_by="alice",
            type="document",
        )
        restored = _ADAPTER.validate_python(original.model_dump())
        assert restored == original

    @pytest.mark.unit
    def test_memory_fine_tune_progress_round_trip(self) -> None:
        """Memory fine-tune progress payload survives a round-trip."""
        original = WsMemoryFineTuneProgressPayload(
            run_id="run-1",
            stage="generating_data",
            progress=0.42,
        )
        restored = _ADAPTER.validate_python(original.model_dump())
        assert restored == original

    @pytest.mark.unit
    def test_request_submitted_round_trip(self) -> None:
        """Request submitted payload survives a round-trip."""
        original = WsRequestSubmittedPayload(
            request_id="req-1",
            client_id="client-1",
            status="submitted",
        )
        restored = _ADAPTER.validate_python(original.model_dump())
        assert restored == original


class TestValidationErrors:
    """Common shape mismatches raise ValidationError."""

    @pytest.mark.unit
    def test_blank_id_rejected(self) -> None:
        """A whitespace-only NotBlankStr field is rejected."""
        with pytest.raises(ValidationError):
            WsTaskCreatedPayload(
                task_id="   ",
                title="Hello",
                status="pending",
            )

    @pytest.mark.unit
    def test_missing_required_field_rejected(self) -> None:
        """Omitting a required field is rejected."""
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(
                {"event_type": "agent.created", "name": "alice"},
            )

    @pytest.mark.unit
    def test_extra_field_rejected(self) -> None:
        """Passing an unknown key is rejected (extra=forbid)."""
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(
                {
                    "event_type": "agent.deleted",
                    "name": "alice",
                    "smuggled": "field",
                },
            )

    @pytest.mark.unit
    def test_wrong_event_type_for_variant_rejected(self) -> None:
        """Constructing a variant with the wrong event_type literal fails."""
        with pytest.raises(ValidationError):
            WsAgentDeletedPayload.model_validate(
                {"event_type": "agent.created", "name": "alice"},
            )

    @pytest.mark.unit
    def test_negative_progress_rejected(self) -> None:
        """Simulation progress outside [0.0, 1.0] is rejected."""
        with pytest.raises(ValidationError):
            WsSimulationStartedPayload(
                simulation_id="s1",
                status="running",
                progress=-0.1,
            )

    @pytest.mark.unit
    def test_negative_token_count_rejected(self) -> None:
        """Personality trim token counts cannot be negative."""
        with pytest.raises(ValidationError):
            WsPersonalityTrimmedPayload(
                agent_id="a1",
                agent_name="Alice",
                task_id="t1",
                trim_tier="aggressive",
                before_tokens=-1,
                after_tokens=500,
            )


class TestSharedBaseShape:
    """Shared bases ensure same wire shape across the four approval events."""

    @pytest.mark.unit
    def test_all_approval_events_have_same_fields(self) -> None:
        """All four approval payloads share the same field set + types."""
        variants_by_name = {v.__name__: v for v in _union_variants()}
        approval_models = [
            variants_by_name["WsApprovalSubmittedPayload"],
            variants_by_name["WsApprovalApprovedPayload"],
            variants_by_name["WsApprovalRejectedPayload"],
            variants_by_name["WsApprovalExpiredPayload"],
        ]
        # Same field set; only event_type's default differs.
        common_fields = {
            f for f in approval_models[0].model_fields if f != "event_type"
        }
        for model in approval_models[1:]:
            other_fields = {f for f in model.model_fields if f != "event_type"}
            assert other_fields == common_fields

    @pytest.mark.unit
    def test_approval_expired_round_trip_with_realistic_payload(self) -> None:
        """The actual emit-site shape from app_helpers.py:66 round-trips."""
        original = WsApprovalExpiredPayload(
            approval_id="approval-1",
            status="expired",
            action_type="agent_action",
            risk_level="medium",
        )
        restored = _ADAPTER.validate_python(original.model_dump())
        assert restored == original
