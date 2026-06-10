"""DTO validation tests for the scaling controller response models."""

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.scaling import (
    PriorityUpdateRequest,
    ScalingDecisionResponse,
    ScalingSignalResponse,
    ScalingStrategyResponse,
)
from tests._shared import JsonDict


def _valid_decision_kwargs(**overrides: object) -> JsonDict:
    base: JsonDict = {
        "id": "decision-1",
        "action_type": "hire",
        "source_strategy": "skill-gap",
        "rationale": "coverage",
        "confidence": 0.8,
        "signals": (),
        "created_at": "2026-04-16T12:00:00Z",
    }
    base.update(overrides)
    return base


@pytest.mark.unit
class TestScalingStrategyResponse:
    """NotBlankStr enforcement on the strategy summary DTO."""

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_rejects_blank_name(self, value: str) -> None:
        with pytest.raises(ValidationError):
            ScalingStrategyResponse(name=value, enabled=True, priority=0)

    def test_rejects_negative_priority(self) -> None:
        with pytest.raises(ValidationError):
            ScalingStrategyResponse(name="workload", enabled=True, priority=-1)

    def test_accepts_valid_payload(self) -> None:
        dto = ScalingStrategyResponse(name="workload", enabled=True, priority=0)
        assert dto.name == "workload"

    def test_is_frozen(self) -> None:
        dto = ScalingStrategyResponse(name="workload", enabled=True, priority=0)
        with pytest.raises(ValidationError):
            dto.name = "other"  # type: ignore[misc]


@pytest.mark.unit
class TestScalingSignalResponse:
    """NotBlankStr enforcement on the signal DTO."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("name", "", id="blank_name"),
            pytest.param("name", "   ", id="whitespace_name"),
            pytest.param("source", "", id="blank_source"),
            pytest.param("source", "\t", id="whitespace_source"),
            pytest.param("timestamp", "", id="blank_timestamp"),
            pytest.param("timestamp", " ", id="whitespace_timestamp"),
        ],
    )
    def test_rejects_blank_identifier_field(self, field: str, value: str) -> None:
        kwargs: JsonDict = {
            "name": "latency",
            "value": 1.0,
            "source": "metrics",
            "timestamp": "2026-04-16T12:00:00Z",
        }
        kwargs[field] = value
        with pytest.raises(ValidationError):
            ScalingSignalResponse(**kwargs)

    def test_rejects_nan_value(self) -> None:
        with pytest.raises(ValidationError):
            ScalingSignalResponse(
                name="latency",
                value=float("nan"),
                source="metrics",
                timestamp="2026-04-16T12:00:00Z",
            )

    def test_accepts_optional_threshold_none(self) -> None:
        dto = ScalingSignalResponse(
            name="latency",
            value=0.9,
            source="metrics",
            timestamp="2026-04-16T12:00:00Z",
        )
        assert dto.threshold is None


@pytest.mark.unit
class TestScalingDecisionResponse:
    """NotBlankStr enforcement across all decision identifier fields."""

    @pytest.mark.parametrize(
        "field",
        ["id", "action_type", "source_strategy", "created_at"],
    )
    @pytest.mark.parametrize("value", ["", "   "])
    def test_rejects_blank_required_identifier(
        self,
        field: str,
        value: str,
    ) -> None:
        kwargs = _valid_decision_kwargs(**{field: value})
        with pytest.raises(ValidationError):
            ScalingDecisionResponse(**kwargs)

    @pytest.mark.parametrize(
        "field",
        ["target_agent_id", "target_role", "target_department"],
    )
    @pytest.mark.parametrize("value", ["", "   "])
    def test_rejects_blank_optional_identifier(
        self,
        field: str,
        value: str,
    ) -> None:
        kwargs = _valid_decision_kwargs(**{field: value})
        with pytest.raises(ValidationError):
            ScalingDecisionResponse(**kwargs)

    def test_accepts_none_for_optional_identifiers(self) -> None:
        dto = ScalingDecisionResponse(
            **_valid_decision_kwargs(
                target_agent_id=None,
                target_role=None,
                target_department=None,
            ),
        )
        assert dto.target_agent_id is None
        assert dto.target_role is None
        assert dto.target_department is None

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_rejects_blank_skill_in_tuple(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            ScalingDecisionResponse(
                **_valid_decision_kwargs(target_skills=("python", blank)),
            )

    def test_accepts_empty_skills_tuple(self) -> None:
        dto = ScalingDecisionResponse(**_valid_decision_kwargs(target_skills=()))
        assert dto.target_skills == ()

    def test_rejects_nan_confidence(self) -> None:
        with pytest.raises(ValidationError):
            ScalingDecisionResponse(
                **_valid_decision_kwargs(confidence=float("nan")),
            )


@pytest.mark.unit
class TestPriorityUpdateRequest:
    """NotBlankStr enforcement on the priority-order request DTO."""

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_rejects_blank_in_order(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            PriorityUpdateRequest(order=("workload", blank))

    def test_accepts_non_blank_order(self) -> None:
        dto = PriorityUpdateRequest(order=("workload", "budget"))
        assert dto.order == ("workload", "budget")
