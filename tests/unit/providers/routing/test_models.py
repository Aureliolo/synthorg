"""Tests for routing domain models."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import SeniorityLevel
from synthorg.providers.routing.models import (
    ResolvedModel,
    RoutingDecision,
    RoutingRequest,
)
from tests.unit.providers.routing.conftest import (
    ResolvedModelFactory,
    RoutingDecisionFactory,
    RoutingRequestFactory,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestResolvedModel:
    def test_build_from_factory(self) -> None:
        model = ResolvedModelFactory.build()
        assert model.provider_name == "test-provider"
        assert model.model_id == "test-medium-001"

    def test_frozen(self) -> None:
        model = ResolvedModelFactory.build()
        with pytest.raises(ValidationError):
            model.model_id = "changed"  # type: ignore[misc]

    def test_alias_optional(self) -> None:
        model = ResolvedModel(
            provider_name="test",
            model_id="test-model",
        )
        assert model.alias is None

    def test_cost_defaults_to_zero(self) -> None:
        model = ResolvedModel(
            provider_name="test",
            model_id="test-model",
        )
        assert model.cost_per_1k_input == 0.0
        assert model.cost_per_1k_output == 0.0

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="greater than or equal",
        ):
            ResolvedModel(
                provider_name="test",
                model_id="test-model",
                cost_per_1k_input=-1.0,
            )

    def test_max_context_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="greater than"):
            ResolvedModel(
                provider_name="test",
                model_id="test-model",
                max_context=0,
            )

    def test_blank_provider_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResolvedModel(provider_name="", model_id="test")

    def test_blank_model_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResolvedModel(provider_name="test", model_id="")

    def test_total_cost_per_1k(self) -> None:
        model = ResolvedModel(
            provider_name="test",
            model_id="test-model",
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
        )
        assert model.total_cost_per_1k == pytest.approx(0.018)

    def test_estimated_latency_ms_default_none(self) -> None:
        model = ResolvedModel(
            provider_name="test",
            model_id="test-model",
        )
        assert model.estimated_latency_ms is None

    def test_estimated_latency_ms_valid(self) -> None:
        model = ResolvedModel(
            provider_name="test",
            model_id="test-model",
            estimated_latency_ms=200,
        )
        assert model.estimated_latency_ms == 200

    def test_estimated_latency_ms_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than"):
            ResolvedModel(
                provider_name="test",
                model_id="test-model",
                estimated_latency_ms=0,
            )

    def test_estimated_latency_ms_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than"):
            ResolvedModel(
                provider_name="test",
                model_id="test-model",
                estimated_latency_ms=-100,
            )

    def test_estimated_latency_ms_exceeds_upper_bound(self) -> None:
        with pytest.raises(ValidationError, match="less than or equal"):
            ResolvedModel(
                provider_name="test",
                model_id="test-model",
                estimated_latency_ms=300_001,
            )

    def test_inf_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResolvedModel(
                provider_name="test",
                model_id="test-model",
                cost_per_1k_input=float("inf"),
            )

    def test_nan_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResolvedModel(
                provider_name="test",
                model_id="test-model",
                cost_per_1k_input=float("nan"),
            )


class TestRoutingRequest:
    def test_build_from_factory(self) -> None:
        request = RoutingRequestFactory.build()
        assert request.agent_level is None
        assert request.task_type is None

    def test_all_fields_optional(self) -> None:
        request = RoutingRequest()
        assert request.agent_level is None
        assert request.task_type is None
        assert request.model_override is None
        assert request.remaining_budget is None

    def test_inf_budget_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingRequest(remaining_budget=float("inf"))

    def test_nan_budget_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingRequest(remaining_budget=float("nan"))

    def test_negative_budget_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="greater than or equal",
        ):
            RoutingRequest(remaining_budget=-1.0)

    def test_blank_task_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingRequest(task_type="")

    def test_whitespace_task_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingRequest(task_type="   ")

    def test_blank_model_override_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingRequest(model_override="")

    def test_whitespace_model_override_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingRequest(model_override="  ")

    def test_with_all_fields(self) -> None:
        request = RoutingRequest(
            agent_level=SeniorityLevel.SENIOR,
            task_type="development",
            model_override="medium",
            remaining_budget=10.0,
        )
        assert request.agent_level == SeniorityLevel.SENIOR
        assert request.task_type == "development"
        assert request.model_override == "medium"
        assert request.remaining_budget == 10.0


class TestRoutingDecision:
    def test_build_from_factory(self) -> None:
        decision = RoutingDecisionFactory.build()
        assert decision.strategy_used == "manual"

    def test_fallbacks_default_empty(self) -> None:
        model = ResolvedModelFactory.build()
        decision = RoutingDecision(
            resolved_model=model,
            strategy_used="test",
            reason="test",
        )
        assert decision.fallbacks_tried == ()

    def test_frozen(self) -> None:
        decision = RoutingDecisionFactory.build()
        with pytest.raises(ValidationError):
            decision.strategy_used = "changed"  # type: ignore[misc]
