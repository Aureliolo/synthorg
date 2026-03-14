"""Tests for output scan response policies."""

import pytest

from synthorg.core.enums import AutonomyLevel, ToolCategory
from synthorg.security.autonomy.models import EffectiveAutonomy
from synthorg.security.models import OutputScanResult, ScanOutcome, SecurityContext
from synthorg.security.output_scan_policy import (
    _DEFAULT_AUTONOMY_POLICY_MAP,
    AutonomyTieredPolicy,
    LogOnlyPolicy,
    OutputScanResponsePolicy,
    RedactPolicy,
    WithholdPolicy,
)

pytestmark = pytest.mark.timeout(30)


# ── Helpers ──────────────────────────────────────────────────────


def _make_context() -> SecurityContext:
    return SecurityContext(
        tool_name="test-tool",
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type="code:read",
        arguments={"path": "/workspace/test"},
        agent_id="agent-1",
        task_id="task-1",
    )


def _sensitive_result() -> OutputScanResult:
    return OutputScanResult(
        has_sensitive_data=True,
        findings=("API key detected",),
        redacted_content="output with [REDACTED]",
        outcome=ScanOutcome.REDACTED,
    )


def _clean_result() -> OutputScanResult:
    return OutputScanResult()


# ── TestRedactPolicy ─────────────────────────────────────────────


@pytest.mark.unit
class TestRedactPolicy:
    """RedactPolicy returns scan result unchanged."""

    def test_sensitive_result_passes_through(self) -> None:
        policy = RedactPolicy()
        result = _sensitive_result()

        transformed = policy.apply(result, _make_context())

        assert transformed == result
        assert transformed.redacted_content == "output with [REDACTED]"
        assert transformed.outcome == ScanOutcome.REDACTED

    def test_clean_result_passes_through(self) -> None:
        policy = RedactPolicy()
        result = _clean_result()

        transformed = policy.apply(result, _make_context())

        assert transformed == result
        assert transformed.has_sensitive_data is False


# ── TestWithholdPolicy ───────────────────────────────────────────


@pytest.mark.unit
class TestWithholdPolicy:
    """WithholdPolicy clears redacted_content on sensitive results."""

    def test_sensitive_result_clears_redacted_content(self) -> None:
        policy = WithholdPolicy()
        result = _sensitive_result()

        transformed = policy.apply(result, _make_context())

        assert transformed.has_sensitive_data is True
        assert transformed.redacted_content is None
        assert transformed.outcome == ScanOutcome.WITHHELD
        # Original result is not mutated (immutability contract).
        assert result.redacted_content == "output with [REDACTED]"

    def test_sensitive_result_preserves_findings(self) -> None:
        policy = WithholdPolicy()
        result = _sensitive_result()

        transformed = policy.apply(result, _make_context())

        assert transformed.findings == ("API key detected",)

    def test_clean_result_unchanged(self) -> None:
        policy = WithholdPolicy()
        result = _clean_result()

        transformed = policy.apply(result, _make_context())

        assert transformed == result


# ── TestLogOnlyPolicy ────────────────────────────────────────────


@pytest.mark.unit
class TestLogOnlyPolicy:
    """LogOnlyPolicy returns empty result regardless of findings."""

    def test_sensitive_result_returns_empty(self) -> None:
        policy = LogOnlyPolicy()
        result = _sensitive_result()

        transformed = policy.apply(result, _make_context())

        assert transformed.has_sensitive_data is False
        assert transformed.findings == ()
        assert transformed.redacted_content is None
        assert transformed.outcome == ScanOutcome.LOG_ONLY

    def test_clean_result_returns_empty(self) -> None:
        policy = LogOnlyPolicy()
        result = _clean_result()

        transformed = policy.apply(result, _make_context())

        assert transformed == OutputScanResult()
        assert transformed.outcome == ScanOutcome.CLEAN


# ── TestAutonomyTieredPolicy ─────────────────────────────────────


@pytest.mark.unit
class TestAutonomyTieredPolicy:
    """AutonomyTieredPolicy delegates based on autonomy level."""

    @staticmethod
    def _make_autonomy(level: AutonomyLevel) -> EffectiveAutonomy:
        return EffectiveAutonomy(
            level=level,
            auto_approve_actions=frozenset({"code:read"}),
            human_approval_actions=frozenset({"deploy:production"}),
            security_agent=False,
        )

    def test_full_autonomy_uses_log_only(self) -> None:
        """FULL level delegates to LogOnlyPolicy (default map)."""
        autonomy = self._make_autonomy(AutonomyLevel.FULL)
        policy = AutonomyTieredPolicy(effective_autonomy=autonomy)
        result = _sensitive_result()

        transformed = policy.apply(result, _make_context())

        # LogOnlyPolicy returns empty result with LOG_ONLY outcome.
        assert transformed.has_sensitive_data is False
        assert transformed.outcome == ScanOutcome.LOG_ONLY

    def test_semi_autonomy_uses_redact(self) -> None:
        """SEMI level delegates to RedactPolicy (default map)."""
        autonomy = self._make_autonomy(AutonomyLevel.SEMI)
        policy = AutonomyTieredPolicy(effective_autonomy=autonomy)
        result = _sensitive_result()

        transformed = policy.apply(result, _make_context())

        # RedactPolicy passes through unchanged.
        assert transformed == result

    def test_locked_autonomy_uses_withhold(self) -> None:
        """LOCKED level delegates to WithholdPolicy (default map)."""
        autonomy = self._make_autonomy(AutonomyLevel.LOCKED)
        policy = AutonomyTieredPolicy(effective_autonomy=autonomy)
        result = _sensitive_result()

        transformed = policy.apply(result, _make_context())

        # WithholdPolicy clears redacted_content.
        assert transformed.has_sensitive_data is True
        assert transformed.redacted_content is None
        assert transformed.outcome == ScanOutcome.WITHHELD

    def test_no_autonomy_falls_back_to_redact(self) -> None:
        """When effective_autonomy is None, falls back to RedactPolicy."""
        policy = AutonomyTieredPolicy(effective_autonomy=None)
        result = _sensitive_result()

        transformed = policy.apply(result, _make_context())

        # Fallback is RedactPolicy — passes through unchanged.
        assert transformed == result

    def test_supervised_autonomy_uses_redact(self) -> None:
        """SUPERVISED level delegates to RedactPolicy (default map)."""
        autonomy = self._make_autonomy(AutonomyLevel.SUPERVISED)
        policy = AutonomyTieredPolicy(effective_autonomy=autonomy)
        result = _sensitive_result()

        transformed = policy.apply(result, _make_context())

        # RedactPolicy passes through unchanged.
        assert transformed == result

    def test_custom_policy_map(self) -> None:
        """Custom policy_map overrides defaults."""
        autonomy = self._make_autonomy(AutonomyLevel.FULL)
        custom_map = {AutonomyLevel.FULL: WithholdPolicy()}
        policy = AutonomyTieredPolicy(
            effective_autonomy=autonomy,
            policy_map=custom_map,
        )
        result = _sensitive_result()

        transformed = policy.apply(result, _make_context())

        # Custom map uses WithholdPolicy for FULL.
        assert transformed.has_sensitive_data is True
        assert transformed.redacted_content is None

    def test_custom_map_missing_level_falls_back_to_redact(self) -> None:
        """Missing level in custom map falls back to RedactPolicy."""
        autonomy = self._make_autonomy(AutonomyLevel.SEMI)
        # Custom map only has FULL — SEMI is missing.
        custom_map = {AutonomyLevel.FULL: WithholdPolicy()}
        policy = AutonomyTieredPolicy(
            effective_autonomy=autonomy,
            policy_map=custom_map,
        )
        result = _sensitive_result()

        transformed = policy.apply(result, _make_context())

        # Fallback is RedactPolicy — passes through unchanged.
        assert transformed == result


# ── Protocol compliance ──────────────────────────────────────────


@pytest.mark.unit
class TestProtocolCompliance:
    """All concrete strategies satisfy OutputScanResponsePolicy."""

    @pytest.mark.parametrize(
        "policy_cls",
        [RedactPolicy, WithholdPolicy, LogOnlyPolicy],
    )
    def test_concrete_satisfies_protocol(
        self,
        policy_cls: type,
    ) -> None:
        instance = policy_cls()
        assert isinstance(instance, OutputScanResponsePolicy)

    def test_autonomy_tiered_satisfies_protocol(self) -> None:
        instance = AutonomyTieredPolicy(effective_autonomy=None)
        assert isinstance(instance, OutputScanResponsePolicy)


# ── Default map integrity ─────────────────────────────────────────


@pytest.mark.unit
class TestDefaultAutonomyPolicyMap:
    """Verify _DEFAULT_AUTONOMY_POLICY_MAP stays in sync with AutonomyLevel."""

    def test_default_map_covers_all_autonomy_levels(self) -> None:
        """Every AutonomyLevel member has an entry in the default map."""
        assert set(_DEFAULT_AUTONOMY_POLICY_MAP.keys()) == set(AutonomyLevel)
