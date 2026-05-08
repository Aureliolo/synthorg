"""Unit tests for the cross-deployment analytics anonymizer."""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import ProposalOutcome
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import (
    ImprovementProposal,
    ProposalAltitude,
    RolloutResult,
)
from synthorg.meta.telemetry.anonymizer import (
    anonymize_decision,
    anonymize_rollout,
)
from synthorg.meta.telemetry.config import CrossDeploymentAnalyticsConfig

from .conftest import BUILTIN_RULE_NAMES

pytestmark = pytest.mark.unit


class TestAnonymizeDecision:
    """Tests for anonymize_decision()."""

    def test_event_type_is_proposal_decision(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.event_type == "proposal_decision"

    def test_altitude_preserved(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.altitude == ProposalAltitude.CONFIG_TUNING.value

    def test_decision_preserved(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.decision == "approved"

    def test_confidence_preserved(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.confidence == 0.72

    def test_builtin_rule_name_preserved(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.source_rule == "coordination_overhead"

    def test_custom_rule_name_masked(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        custom = sample_outcome.model_copy(
            update={"source_rule": NotBlankStr("my_secret_rule")},
        )
        event = anonymize_decision(
            custom,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.source_rule == "custom"

    def test_no_rule_stays_none(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        no_rule = sample_outcome.model_copy(update={"source_rule": None})
        event = anonymize_decision(
            no_rule,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.source_rule is None

    def test_timestamp_coarsened_to_date(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        # Day granularity only -- no time component.
        assert event.timestamp == "2026-04-16"

    def test_deployment_id_is_salted_hash(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        # Must be a hex SHA-256 (64 chars).
        assert len(event.deployment_id) == 64
        assert all(c in "0123456789abcdef" for c in event.deployment_id)

    def test_deployment_id_deterministic(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        e1 = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        e2 = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert e1.deployment_id == e2.deployment_id

    def test_different_salt_different_id(
        self,
        sample_outcome: ProposalOutcome,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        cfg1 = CrossDeploymentAnalyticsConfig(
            enabled=True,
            collector_url=NotBlankStr("https://test.example"),
            deployment_id_salt=NotBlankStr("salt-A"),
        )
        cfg2 = CrossDeploymentAnalyticsConfig(
            enabled=True,
            collector_url=NotBlankStr("https://test.example"),
            deployment_id_salt=NotBlankStr("salt-B"),
        )
        si1 = self_improvement_config.model_copy(
            update={"cross_deployment_analytics": cfg1},
        )
        si2 = self_improvement_config.model_copy(
            update={"cross_deployment_analytics": cfg2},
        )
        e1 = anonymize_decision(
            sample_outcome,
            analytics_config=cfg1,
            self_improvement_config=si1,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        e2 = anonymize_decision(
            sample_outcome,
            analytics_config=cfg2,
            self_improvement_config=si2,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert e1.deployment_id != e2.deployment_id

    def test_enabled_altitudes_from_config(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        # Config has config_tuning + architecture enabled.
        assert "config_tuning" in event.enabled_altitudes
        assert "architecture" in event.enabled_altitudes
        assert "prompt_tuning" not in event.enabled_altitudes

    def test_industry_tag_from_config(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.industry_tag == "technology"

    def test_pii_fields_not_in_event(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        serialized = event.model_dump_json()
        # Must NOT contain PII from the original outcome.
        assert "admin-user" not in serialized
        assert "Looks good" not in serialized
        assert "Reduce coordination" not in serialized

    def test_rollout_fields_are_none(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.rollout_outcome is None
        assert event.regression_verdict is None
        assert event.observation_hours is None

    def test_schema_version(
        self,
        sample_outcome: ProposalOutcome,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_decision(
            sample_outcome,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.schema_version == "1"


class TestAnonymizeRollout:
    """Tests for anonymize_rollout()."""

    def test_event_type_is_rollout_result(
        self,
        sample_rollout_result: RolloutResult,
        sample_proposal: ImprovementProposal,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_rollout(
            sample_rollout_result,
            proposal=sample_proposal,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.event_type == "rollout_result"

    def test_rollout_outcome_preserved(
        self,
        sample_rollout_result: RolloutResult,
        sample_proposal: ImprovementProposal,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_rollout(
            sample_rollout_result,
            proposal=sample_proposal,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.rollout_outcome == "success"

    def test_regression_verdict_preserved(
        self,
        sample_rollout_result: RolloutResult,
        sample_proposal: ImprovementProposal,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_rollout(
            sample_rollout_result,
            proposal=sample_proposal,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.regression_verdict == "no_regression"

    def test_observation_hours_preserved(
        self,
        sample_rollout_result: RolloutResult,
        sample_proposal: ImprovementProposal,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_rollout(
            sample_rollout_result,
            proposal=sample_proposal,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.observation_hours == 48.0

    def test_altitude_from_proposal(
        self,
        sample_rollout_result: RolloutResult,
        sample_proposal: ImprovementProposal,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_rollout(
            sample_rollout_result,
            proposal=sample_proposal,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.altitude == "config_tuning"

    def test_source_rule_from_proposal(
        self,
        sample_rollout_result: RolloutResult,
        sample_proposal: ImprovementProposal,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_rollout(
            sample_rollout_result,
            proposal=sample_proposal,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.source_rule == "coordination_overhead"

    def test_decision_field_is_none(
        self,
        sample_rollout_result: RolloutResult,
        sample_proposal: ImprovementProposal,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_rollout(
            sample_rollout_result,
            proposal=sample_proposal,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert event.decision is None

    def test_details_not_in_event(
        self,
        sample_rollout_result: RolloutResult,
        sample_proposal: ImprovementProposal,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        event = anonymize_rollout(
            sample_rollout_result,
            proposal=sample_proposal,
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        serialized = event.model_dump_json()
        assert "Observation window completed" not in serialized


class TestAnonymizeDecisionProperties:
    """Hypothesis property-based tests for anonymizer."""

    @given(
        salt=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    )
    @settings(
        # ``differing_executors`` mirrors the ``ci`` profile (see
        # ``tests/conftest.py``); a per-test ``@settings`` replaces
        # the profile's ``suppress_health_check`` list rather than
        # extending it, so it must be re-listed here or the check
        # fires on the second pytest-repeat iteration ``[2-2]`` when
        # the isolation gate replays the test under a fresh executor.
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.differing_executors,
        ],
    )
    def test_deployment_id_always_64_hex_chars(
        self,
        salt: str,
        sample_outcome: ProposalOutcome,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        cfg = CrossDeploymentAnalyticsConfig(
            enabled=True,
            collector_url=NotBlankStr("https://test.example"),
            deployment_id_salt=NotBlankStr(salt),
        )
        si = self_improvement_config.model_copy(
            update={"cross_deployment_analytics": cfg},
        )
        event = anonymize_decision(
            sample_outcome,
            analytics_config=cfg,
            self_improvement_config=si,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        assert len(event.deployment_id) == 64
        # Valid hex.
        int(event.deployment_id, 16)

    @given(
        salt=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    )
    @settings(
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.differing_executors,
        ],
    )
    def test_no_pii_in_serialized_output(
        self,
        salt: str,
        sample_outcome: ProposalOutcome,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        cfg = CrossDeploymentAnalyticsConfig(
            enabled=True,
            collector_url=NotBlankStr("https://test.example"),
            deployment_id_salt=NotBlankStr(salt),
        )
        si = self_improvement_config.model_copy(
            update={"cross_deployment_analytics": cfg},
        )
        event = anonymize_decision(
            sample_outcome,
            analytics_config=cfg,
            self_improvement_config=si,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        serialized = event.model_dump_json()
        # PII from the original outcome must never appear.
        assert "admin-user" not in serialized
        assert "Looks good" not in serialized
        assert str(sample_outcome.proposal_id) not in serialized
