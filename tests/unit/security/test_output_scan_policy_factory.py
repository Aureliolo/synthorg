"""Tests for the output scan policy factory."""

import pytest
from typeguard import suppress_type_checks

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.types import NotBlankStr
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.config import OutputScanPolicyType
from synthorg.security.models import (
    OutputScanResult,
    ScanOutcome,
    SecurityContext,
)
from synthorg.security.output_scan_policy import (
    AutonomyTieredPolicy,
    LogOnlyPolicy,
    RedactPolicy,
    WithholdPolicy,
)
from synthorg.security.output_scan_policy_factory import (
    build_output_scan_policy,
)


def _context() -> SecurityContext:
    return SecurityContext(
        tool_name=NotBlankStr("forge_open_issue"),
        tool_category=ToolCategory.VERSION_CONTROL,
        action_type=NotBlankStr("code:write"),
    )


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

    def test_autonomy_tiered_without_autonomy_responds_at_the_strictest_tier(
        self,
    ) -> None:
        """With no tier to read, the response is the ceiling, not the middle.

        Substituting the middle ``RedactPolicy`` handed a LOCKED organisation
        a weaker response than it chose and said nothing about it.
        """
        policy = build_output_scan_policy(
            OutputScanPolicyType.AUTONOMY_TIERED,
            effective_autonomy=None,
        )
        scanned = OutputScanResult(
            redacted_content="token [REDACTED] tail",
            findings=(NotBlankStr("api_key"),),
            has_sensitive_data=True,
            outcome=ScanOutcome.REDACTED,
        )

        applied = policy.apply(scanned, _context())

        assert isinstance(policy, AutonomyTieredPolicy)
        assert applied.outcome is ScanOutcome.WITHHELD
        assert applied.redacted_content is None

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
        with (
            suppress_type_checks(),
            pytest.raises(TypeError, match="Unknown output scan policy type"),
        ):
            build_output_scan_policy("invalid_type")  # type: ignore[arg-type]
