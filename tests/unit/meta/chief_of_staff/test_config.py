"""Unit tests for Chief of Staff configuration."""

import pytest
from pydantic import ValidationError

from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import RuleSeverity

pytestmark = pytest.mark.unit


class TestChiefOfStaffConfig:
    """ChiefOfStaffConfig model tests."""

    def test_defaults_all_disabled(self) -> None:
        cfg = ChiefOfStaffConfig()
        assert cfg.learning_enabled is False
        assert cfg.alerts_enabled is False
        assert cfg.chat_enabled is False

    def test_default_adjuster_strategy(self) -> None:
        cfg = ChiefOfStaffConfig()
        assert cfg.adjuster_strategy == "ema"

    def test_default_ema_alpha(self) -> None:
        cfg = ChiefOfStaffConfig()
        assert cfg.ema_alpha == pytest.approx(0.5)

    def test_default_min_outcomes(self) -> None:
        cfg = ChiefOfStaffConfig()
        assert cfg.min_outcomes == 3

    def test_default_inflection_interval(self) -> None:
        cfg = ChiefOfStaffConfig()
        assert cfg.inflection_check_interval_minutes == 15

    def test_default_severity_threshold(self) -> None:
        cfg = ChiefOfStaffConfig()
        assert cfg.inflection_severity_threshold is RuleSeverity.WARNING

    def test_default_chat_model(self) -> None:
        cfg = ChiefOfStaffConfig()
        assert cfg.chat_model == "example-small-001"

    def test_default_chat_temperature(self) -> None:
        cfg = ChiefOfStaffConfig()
        assert cfg.chat_temperature == pytest.approx(0.7)

    def test_default_chat_max_tokens(self) -> None:
        cfg = ChiefOfStaffConfig()
        assert cfg.chat_max_tokens == 2000

    def test_propose_defaults(self) -> None:
        cfg = ChiefOfStaffConfig()
        assert cfg.propose_enabled is False
        assert cfg.propose_model == "example-small-001"
        assert cfg.propose_temperature == pytest.approx(0.3)
        assert cfg.propose_max_tokens == 2000
        assert cfg.propose_max_proposals_per_turn == 5
        assert cfg.propose_max_clarification_turns == 5

    def test_propose_default_risk_level_medium(self) -> None:
        from synthorg.approval.enums import ApprovalRiskLevel

        cfg = ChiefOfStaffConfig()
        assert cfg.propose_default_risk_level is ApprovalRiskLevel.MEDIUM

    def test_propose_enabled_independent_of_chat(self) -> None:
        cfg = ChiefOfStaffConfig(propose_enabled=True)
        assert cfg.propose_enabled is True
        assert cfg.chat_enabled is False

    def test_propose_max_proposals_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(propose_max_proposals_per_turn=21)

    def test_propose_max_clarification_turns_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(propose_max_clarification_turns=0)

    def test_propose_temperature_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(propose_temperature=2.5)

    def test_narrative_defaults(self) -> None:
        cfg = ChiefOfStaffConfig()
        assert cfg.narrative_enabled is False
        assert cfg.narrative_model == "example-small-001"
        assert cfg.narrative_temperature == pytest.approx(0.4)
        assert cfg.narrative_max_tokens == 2000

    def test_narrative_enabled_independent_of_chat(self) -> None:
        cfg = ChiefOfStaffConfig(narrative_enabled=True)
        assert cfg.narrative_enabled is True
        assert cfg.chat_enabled is False

    def test_narrative_temperature_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(narrative_temperature=2.5)

    def test_narrative_max_tokens_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(narrative_max_tokens=10)

    def test_frozen(self) -> None:
        cfg = ChiefOfStaffConfig()
        with pytest.raises(ValidationError):
            cfg.learning_enabled = True  # type: ignore[misc]

    def test_ema_alpha_bounds_low(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(ema_alpha=-0.1)

    def test_ema_alpha_bounds_high(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(ema_alpha=1.1)

    def test_ema_alpha_boundary_zero(self) -> None:
        cfg = ChiefOfStaffConfig(ema_alpha=0.0)
        assert cfg.ema_alpha == pytest.approx(0.0)

    def test_ema_alpha_boundary_one(self) -> None:
        cfg = ChiefOfStaffConfig(ema_alpha=1.0)
        assert cfg.ema_alpha == pytest.approx(1.0)

    def test_min_outcomes_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(min_outcomes=0)

    def test_inflection_interval_minimum(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(inflection_check_interval_minutes=4)

    def test_inflection_interval_at_minimum(self) -> None:
        cfg = ChiefOfStaffConfig(inflection_check_interval_minutes=5)
        assert cfg.inflection_check_interval_minutes == 5

    def test_invalid_adjuster_strategy(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(adjuster_strategy="neural")  # type: ignore[arg-type]

    def test_invalid_severity_threshold(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(inflection_severity_threshold="debug")  # type: ignore[arg-type]

    def test_bayesian_strategy(self) -> None:
        cfg = ChiefOfStaffConfig(adjuster_strategy="bayesian")
        assert cfg.adjuster_strategy == "bayesian"

    def test_chat_temperature_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(chat_temperature=-0.1)

    def test_chat_max_tokens_minimum(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(chat_max_tokens=99)

    def test_rejects_nan_alpha(self) -> None:
        with pytest.raises(ValidationError):
            ChiefOfStaffConfig(ema_alpha=float("nan"))


class TestSelfImprovementConfigIntegration:
    """ChiefOfStaffConfig integration with SelfImprovementConfig."""

    def test_default_chief_of_staff_config(self) -> None:
        cfg = SelfImprovementConfig()
        assert isinstance(cfg.chief_of_staff, ChiefOfStaffConfig)
        assert cfg.chief_of_staff.learning_enabled is False

    def test_custom_chief_of_staff_config(self) -> None:
        cos_cfg = ChiefOfStaffConfig(
            learning_enabled=True,
            adjuster_strategy="bayesian",
            min_outcomes=5,
        )
        cfg = SelfImprovementConfig(chief_of_staff=cos_cfg)
        assert cfg.chief_of_staff.learning_enabled is True
        assert cfg.chief_of_staff.adjuster_strategy == "bayesian"
        assert cfg.chief_of_staff.min_outcomes == 5

    def test_does_not_break_existing_defaults(self) -> None:
        cfg = SelfImprovementConfig()
        assert cfg.enabled is False
        assert cfg.chief_of_staff_enabled is False
        assert cfg.config_tuning_enabled is True
        assert cfg.schedule.cycle_interval_hours == 168
