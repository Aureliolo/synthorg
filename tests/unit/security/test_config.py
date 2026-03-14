"""Tests for security configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ApprovalRiskLevel
from synthorg.security.config import (
    OutputScanPolicyType,
    RuleEngineConfig,
    SecurityConfig,
    SecurityPolicyRule,
)
from synthorg.security.models import SecurityVerdictType

pytestmark = pytest.mark.timeout(30)


# ── SecurityPolicyRule ───────────────────────────────────────────


@pytest.mark.unit
class TestSecurityPolicyRule:
    """Tests for SecurityPolicyRule defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        rule = SecurityPolicyRule(name="test-rule")
        assert rule.name == "test-rule"
        assert rule.description == ""
        assert rule.action_types == ()
        assert rule.verdict == SecurityVerdictType.DENY
        assert rule.risk_level is ApprovalRiskLevel.MEDIUM
        assert rule.enabled is True

    def test_custom_values(self) -> None:
        rule = SecurityPolicyRule(
            name="block-deploys",
            description="Block all production deploys",
            action_types=("deploy:production", "deploy:staging"),
            verdict=SecurityVerdictType.ESCALATE,
            risk_level=ApprovalRiskLevel.HIGH,
            enabled=False,
        )
        assert rule.name == "block-deploys"
        assert rule.description == "Block all production deploys"
        assert rule.action_types == ("deploy:production", "deploy:staging")
        assert rule.verdict == SecurityVerdictType.ESCALATE
        assert rule.risk_level is ApprovalRiskLevel.HIGH
        assert rule.enabled is False

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SecurityPolicyRule(name="")

    def test_whitespace_only_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace"):
            SecurityPolicyRule(name="   ")

    def test_frozen(self) -> None:
        rule = SecurityPolicyRule(name="immutable-rule")
        with pytest.raises(ValidationError):
            rule.name = "changed"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "verdict",
        [
            SecurityVerdictType.ALLOW,
            SecurityVerdictType.DENY,
            SecurityVerdictType.ESCALATE,
        ],
    )
    def test_all_verdict_types_accepted(self, verdict: SecurityVerdictType) -> None:
        rule = SecurityPolicyRule(name="rule", verdict=verdict)
        assert rule.verdict == verdict

    @pytest.mark.parametrize(
        "risk_level",
        list(ApprovalRiskLevel),
    )
    def test_all_risk_levels_accepted(self, risk_level: ApprovalRiskLevel) -> None:
        rule = SecurityPolicyRule(name="rule", risk_level=risk_level)
        assert rule.risk_level is risk_level

    def test_json_roundtrip(self) -> None:
        rule = SecurityPolicyRule(
            name="roundtrip-rule",
            description="A rule for testing",
            action_types=("code:write", "code:delete"),
            verdict=SecurityVerdictType.ESCALATE,
            risk_level=ApprovalRiskLevel.CRITICAL,
            enabled=True,
        )
        json_str = rule.model_dump_json()
        restored = SecurityPolicyRule.model_validate_json(json_str)
        assert restored == rule


# ── RuleEngineConfig ─────────────────────────────────────────────


@pytest.mark.unit
class TestRuleEngineConfig:
    """Tests for RuleEngineConfig defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        cfg = RuleEngineConfig()
        assert cfg.credential_patterns_enabled is True
        assert cfg.data_leak_detection_enabled is True
        assert cfg.destructive_op_detection_enabled is True
        assert cfg.path_traversal_detection_enabled is True
        assert cfg.max_argument_length == 100_000

    def test_all_detectors_disabled(self) -> None:
        cfg = RuleEngineConfig(
            credential_patterns_enabled=False,
            data_leak_detection_enabled=False,
            destructive_op_detection_enabled=False,
            path_traversal_detection_enabled=False,
        )
        assert cfg.credential_patterns_enabled is False
        assert cfg.data_leak_detection_enabled is False
        assert cfg.destructive_op_detection_enabled is False
        assert cfg.path_traversal_detection_enabled is False

    def test_custom_max_argument_length(self) -> None:
        cfg = RuleEngineConfig(max_argument_length=50_000)
        assert cfg.max_argument_length == 50_000

    def test_max_argument_length_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            RuleEngineConfig(max_argument_length=0)

    def test_negative_max_argument_length_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RuleEngineConfig(max_argument_length=-1)

    def test_frozen(self) -> None:
        cfg = RuleEngineConfig()
        with pytest.raises(ValidationError):
            cfg.credential_patterns_enabled = False  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        cfg = RuleEngineConfig(
            credential_patterns_enabled=False,
            max_argument_length=200_000,
        )
        json_str = cfg.model_dump_json()
        restored = RuleEngineConfig.model_validate_json(json_str)
        assert restored == cfg


# ── SecurityConfig ───────────────────────────────────────────────


@pytest.mark.unit
class TestSecurityConfig:
    """Tests for SecurityConfig defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        cfg = SecurityConfig()
        assert cfg.enabled is True
        assert cfg.audit_enabled is True
        assert cfg.post_tool_scanning_enabled is True
        assert isinstance(cfg.rule_engine, RuleEngineConfig)
        assert cfg.hard_deny_action_types == (
            "deploy:production",
            "db:admin",
            "org:fire",
        )
        assert cfg.auto_approve_action_types == (
            "code:read",
            "docs:write",
        )
        assert cfg.output_scan_policy_type == OutputScanPolicyType.AUTONOMY_TIERED
        assert cfg.custom_policies == ()

    def test_disabled_state(self) -> None:
        cfg = SecurityConfig(enabled=False)
        assert cfg.enabled is False
        # Other defaults still hold.
        assert cfg.audit_enabled is True
        assert cfg.hard_deny_action_types == (
            "deploy:production",
            "db:admin",
            "org:fire",
        )

    def test_custom_hard_deny_action_types(self) -> None:
        cfg = SecurityConfig(
            hard_deny_action_types=("org:fire", "budget:exceed"),
        )
        assert cfg.hard_deny_action_types == ("org:fire", "budget:exceed")

    def test_custom_auto_approve_action_types(self) -> None:
        cfg = SecurityConfig(
            auto_approve_action_types=("code:read",),
        )
        assert cfg.auto_approve_action_types == ("code:read",)

    def test_empty_deny_and_approve_lists(self) -> None:
        cfg = SecurityConfig(
            hard_deny_action_types=(),
            auto_approve_action_types=(),
        )
        assert cfg.hard_deny_action_types == ()
        assert cfg.auto_approve_action_types == ()

    def test_custom_policies(self) -> None:
        policy = SecurityPolicyRule(
            name="no-external-comms",
            description="Block external communication",
            action_types=("comms:external",),
            verdict=SecurityVerdictType.DENY,
            risk_level=ApprovalRiskLevel.HIGH,
        )
        cfg = SecurityConfig(custom_policies=(policy,))
        assert len(cfg.custom_policies) == 1
        assert cfg.custom_policies[0].name == "no-external-comms"

    def test_multiple_custom_policies(self) -> None:
        policies = (
            SecurityPolicyRule(
                name="policy-a",
                action_types=("code:delete",),
            ),
            SecurityPolicyRule(
                name="policy-b",
                action_types=("db:mutate",),
                verdict=SecurityVerdictType.ESCALATE,
            ),
        )
        cfg = SecurityConfig(custom_policies=policies)
        assert len(cfg.custom_policies) == 2

    def test_custom_rule_engine(self) -> None:
        engine = RuleEngineConfig(
            credential_patterns_enabled=False,
            max_argument_length=10_000,
        )
        cfg = SecurityConfig(rule_engine=engine)
        assert cfg.rule_engine.credential_patterns_enabled is False
        assert cfg.rule_engine.max_argument_length == 10_000

    def test_disabled_auditing_and_scanning(self) -> None:
        cfg = SecurityConfig(
            audit_enabled=False,
            post_tool_scanning_enabled=False,
        )
        assert cfg.audit_enabled is False
        assert cfg.post_tool_scanning_enabled is False

    def test_frozen(self) -> None:
        cfg = SecurityConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = False  # type: ignore[misc]

    def test_frozen_nested_rule_engine(self) -> None:
        cfg = SecurityConfig()
        with pytest.raises(ValidationError):
            cfg.rule_engine.max_argument_length = 999  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        policy = SecurityPolicyRule(
            name="roundtrip-policy",
            action_types=("code:write",),
            verdict=SecurityVerdictType.ESCALATE,
            risk_level=ApprovalRiskLevel.HIGH,
        )
        cfg = SecurityConfig(
            enabled=False,
            rule_engine=RuleEngineConfig(max_argument_length=5_000),
            audit_enabled=False,
            post_tool_scanning_enabled=False,
            hard_deny_action_types=("org:fire",),
            auto_approve_action_types=(),
            output_scan_policy_type=OutputScanPolicyType.WITHHOLD,
            custom_policies=(policy,),
        )
        json_str = cfg.model_dump_json()
        restored = SecurityConfig.model_validate_json(json_str)
        assert restored == cfg
        assert restored.output_scan_policy_type == OutputScanPolicyType.WITHHOLD


# ── OutputScanPolicyType ─────────────────────────────────────────


@pytest.mark.unit
class TestOutputScanPolicyType:
    """Tests for OutputScanPolicyType enum values and config integration."""

    @pytest.mark.parametrize(
        "policy_type",
        list(OutputScanPolicyType),
    )
    def test_all_policy_types_accepted_in_config(
        self,
        policy_type: OutputScanPolicyType,
    ) -> None:
        cfg = SecurityConfig(output_scan_policy_type=policy_type)
        assert cfg.output_scan_policy_type == policy_type

    def test_invalid_policy_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SecurityConfig(output_scan_policy_type="nonexistent")  # type: ignore[arg-type]

    def test_enum_values(self) -> None:
        assert OutputScanPolicyType.REDACT.value == "redact"
        assert OutputScanPolicyType.WITHHOLD.value == "withhold"
        assert OutputScanPolicyType.LOG_ONLY.value == "log_only"
        assert OutputScanPolicyType.AUTONOMY_TIERED.value == "autonomy_tiered"
