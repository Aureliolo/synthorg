"""Tests for the output scan policy factory."""

import pytest

from synthorg.core.enums import AutonomyLevel
from synthorg.security.autonomy.models import EffectiveAutonomy
from synthorg.security.config import OutputScanPolicyType
from synthorg.security.output_scan_policy import (
    AutonomyTieredPolicy,
    LogOnlyPolicy,
    RedactPolicy,
    WithholdPolicy,
)
from synthorg.security.output_scan_policy_factory import (
    build_output_scan_policy,
)

pytestmark = pytest.mark.timeout(30)


def _make_autonomy() -> EffectiveAutonomy:
    return EffectiveAutonomy(
        level=AutonomyLevel.SEMI,
        auto_approve_actions=frozenset({"code:read"}),
        human_approval_actions=frozenset({"deploy:production"}),
        security_agent=False,
    )


@pytest.mark.unit
class TestBuildOutputScanPolicy:
    """Factory creates the correct policy for each config enum."""

    def test_redact(self) -> None:
        policy = build_output_scan_policy(OutputScanPolicyType.REDACT)
        assert isinstance(policy, RedactPolicy)
        assert policy.name == "redact"

    def test_withhold(self) -> None:
        policy = build_output_scan_policy(OutputScanPolicyType.WITHHOLD)
        assert isinstance(policy, WithholdPolicy)
        assert policy.name == "withhold"

    def test_log_only(self) -> None:
        policy = build_output_scan_policy(OutputScanPolicyType.LOG_ONLY)
        assert isinstance(policy, LogOnlyPolicy)
        assert policy.name == "log_only"

    def test_autonomy_tiered_with_autonomy(self) -> None:
        autonomy = _make_autonomy()
        policy = build_output_scan_policy(
            OutputScanPolicyType.AUTONOMY_TIERED,
            effective_autonomy=autonomy,
        )
        assert isinstance(policy, AutonomyTieredPolicy)
        assert policy.name == "autonomy_tiered"

    def test_autonomy_tiered_without_autonomy(self) -> None:
        """AUTONOMY_TIERED with no autonomy still returns a policy."""
        policy = build_output_scan_policy(
            OutputScanPolicyType.AUTONOMY_TIERED,
            effective_autonomy=None,
        )
        assert isinstance(policy, AutonomyTieredPolicy)

    def test_effective_autonomy_ignored_for_non_tiered(self) -> None:
        """effective_autonomy is ignored for non-AUTONOMY_TIERED types."""
        autonomy = _make_autonomy()
        policy = build_output_scan_policy(
            OutputScanPolicyType.REDACT,
            effective_autonomy=autonomy,
        )
        assert isinstance(policy, RedactPolicy)

    def test_unknown_policy_type_raises_type_error(self) -> None:
        """Unknown policy type raises TypeError."""
        with pytest.raises(TypeError, match="Unknown output scan policy type"):
            build_output_scan_policy("invalid_type")  # type: ignore[arg-type]
