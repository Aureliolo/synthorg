"""Tests for timeout policy factory."""

import pytest

from ai_company.core.enums import TimeoutActionType
from ai_company.security.timeout.config import (
    DenyOnTimeoutConfig,
    EscalationChainConfig,
    EscalationStep,
    TieredTimeoutConfig,
    WaitForeverConfig,
)
from ai_company.security.timeout.factory import create_timeout_policy
from ai_company.security.timeout.policies import (
    DenyOnTimeoutPolicy,
    EscalationChainPolicy,
    TieredTimeoutPolicy,
    WaitForeverPolicy,
)


class TestFactory:
    """create_timeout_policy returns the correct implementation."""

    @pytest.mark.unit
    def test_wait_forever(self) -> None:
        result = create_timeout_policy(WaitForeverConfig())
        assert isinstance(result, WaitForeverPolicy)

    @pytest.mark.unit
    def test_deny_on_timeout(self) -> None:
        result = create_timeout_policy(DenyOnTimeoutConfig(timeout_minutes=60))
        assert isinstance(result, DenyOnTimeoutPolicy)

    @pytest.mark.unit
    def test_tiered(self) -> None:
        result = create_timeout_policy(TieredTimeoutConfig())
        assert isinstance(result, TieredTimeoutPolicy)

    @pytest.mark.unit
    def test_escalation_chain(self) -> None:
        config = EscalationChainConfig(
            chain=(EscalationStep(role="lead", timeout_minutes=30),),
            on_chain_exhausted=TimeoutActionType.DENY,
        )
        result = create_timeout_policy(config)
        assert isinstance(result, EscalationChainPolicy)
