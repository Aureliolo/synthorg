"""Tests for timeout policy configuration models."""

import pytest
from pydantic import TypeAdapter, ValidationError

from synthorg.core.enums import TimeoutActionType
from synthorg.security.timeout.config import (
    ApprovalTimeoutConfig,
    DenyOnTimeoutConfig,
    EscalationChainConfig,
    EscalationStep,
    TierConfig,
    TieredTimeoutConfig,
    WaitForeverConfig,
)
from synthorg.security.timeout.models import TimeoutAction

_adapter: TypeAdapter[ApprovalTimeoutConfig] = TypeAdapter(ApprovalTimeoutConfig)


class TestWaitForeverConfig:
    """WaitForeverConfig tests."""

    @pytest.mark.unit
    def test_default(self) -> None:
        config = WaitForeverConfig()
        assert config.policy == "wait"

    @pytest.mark.unit
    def test_discriminator(self) -> None:
        result = _adapter.validate_python({"policy": "wait"})
        assert isinstance(result, WaitForeverConfig)


class TestDenyOnTimeoutConfig:
    """DenyOnTimeoutConfig tests."""

    @pytest.mark.unit
    def test_default_timeout(self) -> None:
        config = DenyOnTimeoutConfig()
        assert config.timeout_minutes == 240.0

    @pytest.mark.unit
    def test_custom_timeout(self) -> None:
        config = DenyOnTimeoutConfig(timeout_minutes=60.0)
        assert config.timeout_minutes == 60.0

    @pytest.mark.unit
    def test_discriminator(self) -> None:
        result = _adapter.validate_python({"policy": "deny", "timeout_minutes": 30})
        assert isinstance(result, DenyOnTimeoutConfig)
        assert result.timeout_minutes == 30.0

    @pytest.mark.unit
    def test_zero_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DenyOnTimeoutConfig(timeout_minutes=0)


class TestTieredTimeoutConfig:
    """TieredTimeoutConfig tests."""

    @pytest.mark.unit
    def test_empty_tiers(self) -> None:
        config = TieredTimeoutConfig()
        assert config.tiers == {}

    @pytest.mark.unit
    def test_tier_config(self) -> None:
        tier = TierConfig(
            timeout_minutes=60,
            on_timeout=TimeoutActionType.DENY,
        )
        config = TieredTimeoutConfig(tiers={"high": tier})
        assert "high" in config.tiers
        assert config.tiers["high"].on_timeout == TimeoutActionType.DENY

    @pytest.mark.unit
    def test_discriminator(self) -> None:
        result = _adapter.validate_python(
            {
                "policy": "tiered",
                "tiers": {
                    "low": {"timeout_minutes": 480, "on_timeout": "approve"},
                    "high": {"timeout_minutes": 60, "on_timeout": "deny"},
                },
            }
        )
        assert isinstance(result, TieredTimeoutConfig)
        assert result.tiers["low"].on_timeout == TimeoutActionType.APPROVE


class TestEscalationChainConfig:
    """EscalationChainConfig tests."""

    @pytest.mark.unit
    def test_empty_chain_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one step"):
            EscalationChainConfig()

    @pytest.mark.unit
    def test_chain_steps(self) -> None:
        config = EscalationChainConfig(
            chain=(
                EscalationStep(role="lead", timeout_minutes=30),
                EscalationStep(role="director", timeout_minutes=60),
            ),
            on_chain_exhausted=TimeoutActionType.DENY,
        )
        assert len(config.chain) == 2
        assert config.chain[0].role == "lead"

    @pytest.mark.unit
    def test_discriminator(self) -> None:
        result = _adapter.validate_python(
            {
                "policy": "escalation",
                "chain": [
                    {"role": "lead", "timeout_minutes": 30},
                ],
                "on_chain_exhausted": "deny",
            }
        )
        assert isinstance(result, EscalationChainConfig)
        assert len(result.chain) == 1


class TestTierConfigValidation:
    """TierConfig validator tests."""

    @pytest.mark.unit
    def test_escalate_on_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ESCALATE"):
            TierConfig(
                timeout_minutes=60,
                on_timeout=TimeoutActionType.ESCALATE,
            )


class TestTieredTimeoutConfigValidation:
    """TieredTimeoutConfig validator tests."""

    @pytest.mark.unit
    def test_invalid_tier_key_rejected(self) -> None:
        tier = TierConfig(timeout_minutes=60, on_timeout=TimeoutActionType.DENY)
        with pytest.raises(ValidationError, match="Invalid tier keys"):
            TieredTimeoutConfig(tiers={"invalid_key": tier})

    @pytest.mark.unit
    def test_valid_tier_keys_accepted(self) -> None:
        tier = TierConfig(timeout_minutes=60, on_timeout=TimeoutActionType.DENY)
        config = TieredTimeoutConfig(tiers={"low": tier, "high": tier})
        assert len(config.tiers) == 2


class TestEscalationChainConfigValidation:
    """EscalationChainConfig validator tests."""

    @pytest.mark.unit
    def test_escalate_on_chain_exhausted_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ESCALATE"):
            EscalationChainConfig(
                chain=(EscalationStep(role="lead", timeout_minutes=30),),
                on_chain_exhausted=TimeoutActionType.ESCALATE,
            )


class TestTimeoutAction:
    """TimeoutAction escalate_to validator tests."""

    @pytest.mark.unit
    def test_escalate_without_target_raises(self) -> None:
        with pytest.raises(ValidationError, match="escalate_to is required"):
            TimeoutAction(
                action=TimeoutActionType.ESCALATE,
                reason="test",
            )

    @pytest.mark.unit
    def test_non_escalate_with_target_raises(self) -> None:
        with pytest.raises(ValidationError, match="escalate_to must be None"):
            TimeoutAction(
                action=TimeoutActionType.DENY,
                reason="test",
                escalate_to="lead",
            )
