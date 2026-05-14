"""Tests for ``CustomRuleResponse`` wire-shape DTO."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from synthorg.meta.models import ProposalAltitude, RuleSeverity
from synthorg.meta.rules.custom import (
    Comparator,
    CustomRuleDefinition,
    CustomRuleResponse,
)

pytestmark = pytest.mark.unit


def _make_definition(  # noqa: PLR0913 -- factory builder; all knobs optional
    *,
    rule_id: UUID | None = None,
    name: str = "high-cost",
    description: str = "Spend overshoots target",
    metric_path: str = "budget.total_spend",
    comparator: Comparator = Comparator.GT,
    threshold: float = 1000.0,
    severity: RuleSeverity = RuleSeverity.WARNING,
    target_altitudes: tuple[ProposalAltitude, ...] = (ProposalAltitude.CONFIG_TUNING,),
    enabled: bool = True,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> CustomRuleDefinition:
    fixed_id = rule_id or UUID("12345678-1234-5678-1234-567812345678")
    now = datetime(2026, 5, 14, 10, 0, 0, tzinfo=UTC)
    return CustomRuleDefinition(
        id=fixed_id,
        name=name,
        description=description,
        metric_path=metric_path,
        comparator=comparator,
        threshold=threshold,
        severity=severity,
        target_altitudes=target_altitudes,
        enabled=enabled,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )


class TestCustomRuleResponseFromDefinition:
    """``CustomRuleResponse.from_definition`` projection contract."""

    def test_stringifies_uuid(self) -> None:
        rule_id = UUID("12345678-1234-5678-1234-567812345678")
        defn = _make_definition(rule_id=rule_id)
        response = CustomRuleResponse.from_definition(defn)
        assert response.id == str(rule_id)

    def test_copies_string_fields(self) -> None:
        defn = _make_definition(name="my-rule", description="a useful rule")
        response = CustomRuleResponse.from_definition(defn)
        assert response.name == "my-rule"
        assert response.description == "a useful rule"
        assert response.metric_path == defn.metric_path

    def test_unwraps_comparator_and_severity_to_string_values(self) -> None:
        defn = _make_definition(
            comparator=Comparator.GE,
            severity=RuleSeverity.WARNING,
        )
        response = CustomRuleResponse.from_definition(defn)
        assert response.comparator == "ge"
        assert response.severity == "warning"

    def test_unwraps_target_altitudes_to_string_tuple(self) -> None:
        defn = _make_definition(
            target_altitudes=(
                ProposalAltitude.CONFIG_TUNING,
                ProposalAltitude.ARCHITECTURE,
            ),
        )
        response = CustomRuleResponse.from_definition(defn)
        assert response.target_altitudes == ("config_tuning", "architecture")

    def test_passes_through_threshold_and_enabled(self) -> None:
        defn = _make_definition(threshold=42.5, enabled=False)
        response = CustomRuleResponse.from_definition(defn)
        assert response.threshold == 42.5
        assert response.enabled is False

    def test_isoformats_timestamps(self) -> None:
        ts = datetime(2026, 5, 14, 10, 0, 0, tzinfo=UTC)
        defn = _make_definition(created_at=ts, updated_at=ts)
        response = CustomRuleResponse.from_definition(defn)
        assert response.created_at == ts.isoformat()
        assert response.updated_at == ts.isoformat()


class TestCustomRuleResponseModelDumpRoundTrip:
    """``model_dump(mode="json")`` produces a JSON-shaped envelope dict."""

    def test_round_trip_preserves_values(self) -> None:
        defn = _make_definition()
        response = CustomRuleResponse.from_definition(defn)
        dumped = response.model_dump(mode="json")
        rebuilt = CustomRuleResponse.model_validate(dumped)
        assert rebuilt == response

    def test_dump_emits_string_uuid(self) -> None:
        rule_id = UUID("12345678-1234-5678-1234-567812345678")
        defn = _make_definition(rule_id=rule_id)
        dumped = CustomRuleResponse.from_definition(defn).model_dump(mode="json")
        assert dumped["id"] == str(rule_id)
        assert isinstance(dumped["id"], str)

    def test_dump_emits_string_comparator_and_severity(self) -> None:
        defn = _make_definition(
            comparator=Comparator.LT,
            severity=RuleSeverity.INFO,
        )
        dumped = CustomRuleResponse.from_definition(defn).model_dump(mode="json")
        assert dumped["comparator"] == "lt"
        assert dumped["severity"] == "info"

    def test_dump_emits_list_for_target_altitudes(self) -> None:
        defn = _make_definition(
            target_altitudes=(
                ProposalAltitude.CONFIG_TUNING,
                ProposalAltitude.ARCHITECTURE,
            ),
        )
        dumped = CustomRuleResponse.from_definition(defn).model_dump(mode="json")
        assert dumped["target_altitudes"] == ["config_tuning", "architecture"]

    def test_dump_emits_iso_timestamps(self) -> None:
        ts = datetime(2026, 5, 14, 10, 0, 0, tzinfo=UTC)
        defn = _make_definition(created_at=ts, updated_at=ts)
        dumped = CustomRuleResponse.from_definition(defn).model_dump(mode="json")
        assert dumped["created_at"] == ts.isoformat()
        assert dumped["updated_at"] == ts.isoformat()


class TestCustomRuleResponseModelConfig:
    """The DTO is frozen and extra-forbidden."""

    def test_frozen(self) -> None:
        defn = _make_definition()
        response = CustomRuleResponse.from_definition(defn)
        with pytest.raises(ValidationError):
            response.name = "mutated"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        defn = _make_definition()
        dumped = CustomRuleResponse.from_definition(defn).model_dump(mode="json")
        dumped["unexpected_field"] = "x"
        with pytest.raises(ValidationError):
            CustomRuleResponse.model_validate(dumped)
