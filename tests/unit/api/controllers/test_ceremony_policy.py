"""Tests for ceremony policy controller.

Covers the /ceremony-policy endpoints: project-level query,
resolved policy with field origins, and active strategy.
"""

import pytest
from pydantic import ValidationError

from synthorg.coordination.ceremony_policy.policy_resolver import (
    PolicyFieldOrigin,
    ResolvedCeremonyPolicyResponse,
    ResolvedPolicyField,
    _build_project_policy,
    _build_resolved_response,
    _determine_field_origin,
)
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,
    CeremonyStrategyType,
)
from synthorg.engine.workflow.velocity_types import VelocityCalcType


@pytest.mark.unit
class TestBuildProjectPolicy:
    """Tests for _build_project_policy helper."""

    def test_all_fields_present(self) -> None:
        data = {
            "ceremony_strategy": "calendar",
            "ceremony_strategy_config": '{"duration_days": 14}',
            "ceremony_velocity_calculator": "calendar",
            "ceremony_auto_transition": "false",
            "ceremony_transition_threshold": "0.8",
        }
        policy = _build_project_policy(data)
        assert policy.strategy == CeremonyStrategyType.CALENDAR
        assert policy.strategy_config == {"duration_days": 14}
        assert policy.velocity_calculator == VelocityCalcType.CALENDAR
        assert policy.auto_transition is False
        assert policy.transition_threshold == 0.8

    def test_defaults_when_empty(self) -> None:
        policy = _build_project_policy({})
        assert policy.strategy is None
        assert policy.strategy_config is None
        assert policy.velocity_calculator is None
        assert policy.auto_transition is None
        assert policy.transition_threshold is None

    def test_empty_strategy_config_returns_none(self) -> None:
        data = {"ceremony_strategy_config": "{}"}
        policy = _build_project_policy(data)
        assert policy.strategy_config is None

    def test_non_empty_strategy_config(self) -> None:
        data = {"ceremony_strategy_config": '{"trigger": "sprint_start"}'}
        policy = _build_project_policy(data)
        assert policy.strategy_config == {"trigger": "sprint_start"}


@pytest.mark.unit
class TestDetermineFieldOrigin:
    """Tests for _determine_field_origin helper."""

    def test_department_overrides_project(self) -> None:
        project = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
        )
        department = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.CALENDAR,
        )
        origin = _determine_field_origin("strategy", project, department)
        assert origin == PolicyFieldOrigin.DEPARTMENT

    def test_project_when_department_is_none(self) -> None:
        project = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
        )
        origin = _determine_field_origin("strategy", project, None)
        assert origin == PolicyFieldOrigin.PROJECT

    def test_project_when_department_field_is_none(self) -> None:
        project = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
        )
        department = CeremonyPolicyConfig()  # all None
        origin = _determine_field_origin("strategy", project, department)
        assert origin == PolicyFieldOrigin.PROJECT

    def test_default_when_both_none(self) -> None:
        project = CeremonyPolicyConfig()  # all None
        origin = _determine_field_origin("strategy", project, None)
        assert origin == PolicyFieldOrigin.DEFAULT

    def test_auto_transition_from_department(self) -> None:
        project = CeremonyPolicyConfig(auto_transition=True)
        department = CeremonyPolicyConfig(auto_transition=False)
        origin = _determine_field_origin("auto_transition", project, department)
        assert origin == PolicyFieldOrigin.DEPARTMENT


@pytest.mark.unit
class TestBuildResolvedResponse:
    """Tests for _build_resolved_response helper."""

    def test_project_only(self) -> None:
        project = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.CALENDAR,
            auto_transition=False,
            transition_threshold=0.8,
        )
        response = _build_resolved_response(project, None)
        assert isinstance(response, ResolvedCeremonyPolicyResponse)
        assert response.strategy.value == "calendar"
        assert response.strategy.source == PolicyFieldOrigin.PROJECT
        assert response.auto_transition.value is False
        assert response.auto_transition.source == PolicyFieldOrigin.PROJECT
        assert response.transition_threshold.value == 0.8
        # velocity_calculator defaults based on strategy
        assert response.velocity_calculator.value == "calendar"
        assert response.velocity_calculator.source == PolicyFieldOrigin.DEFAULT

    def test_department_overrides(self) -> None:
        project = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
            auto_transition=True,
            transition_threshold=1.0,
        )
        department = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.CALENDAR,
        )
        response = _build_resolved_response(project, department)
        assert response.strategy.value == "calendar"
        assert response.strategy.source == PolicyFieldOrigin.DEPARTMENT
        # auto_transition comes from project
        assert response.auto_transition.value is True
        assert response.auto_transition.source == PolicyFieldOrigin.PROJECT

    def test_all_defaults(self) -> None:
        project = CeremonyPolicyConfig()
        response = _build_resolved_response(project, None)
        # Framework defaults apply
        assert response.strategy.value == "task_driven"
        assert response.strategy.source == PolicyFieldOrigin.DEFAULT
        assert response.auto_transition.value is True
        assert response.auto_transition.source == PolicyFieldOrigin.DEFAULT
        assert response.transition_threshold.value == 1.0
        assert response.transition_threshold.source == PolicyFieldOrigin.DEFAULT

    def test_response_field_types(self) -> None:
        project = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.HYBRID,
            velocity_calculator=VelocityCalcType.MULTI_DIMENSIONAL,
        )
        response = _build_resolved_response(project, None)
        # Enum values serialized as strings
        assert isinstance(response.strategy.value, str)
        assert isinstance(response.velocity_calculator.value, str)
        assert isinstance(response.auto_transition.value, bool)
        assert isinstance(response.transition_threshold.value, float)


@pytest.mark.unit
class TestResolvedPolicyFieldModel:
    """Tests for the ResolvedPolicyField model."""

    def test_frozen(self) -> None:
        field = ResolvedPolicyField(
            value="task_driven", source=PolicyFieldOrigin.PROJECT
        )
        with pytest.raises(ValidationError, match="frozen"):
            field.value = "calendar"  # type: ignore[misc]

    def test_serialization(self) -> None:
        field = ResolvedPolicyField(
            value="calendar", source=PolicyFieldOrigin.DEPARTMENT
        )
        data = field.model_dump(mode="json")
        assert data["value"] == "calendar"
        assert data["source"] == "department"
