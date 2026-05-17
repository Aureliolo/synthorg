"""Tests for the risk-tier-classifier plugin surface (REWORK #9)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import ApprovalRiskLevel
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.security.timeout.config import TieredTimeoutConfig
from synthorg.security.timeout.errors import RiskClassifierConfigError
from synthorg.security.timeout.factory import create_timeout_policy
from synthorg.security.timeout.operator_configurable import (
    OperatorConfigurableRiskClassifier,
)
from synthorg.security.timeout.policies import TieredTimeoutPolicy
from synthorg.security.timeout.risk_classifier_config import (
    RiskClassifierConfig,
    RiskClassifierDeps,
    RiskClassifierType,
)
from synthorg.security.timeout.risk_classifier_factory import (
    build_risk_tier_classifier,
)
from synthorg.security.timeout.risk_tier_classifier import (
    DefaultRiskTierClassifier,
    elevate_one_tier,
)
from synthorg.security.timeout.time_based_elevation import (
    TimeBasedRiskElevationClassifier,
)
from synthorg.security.timeout.workload_adaptive import (
    WorkloadAdaptiveRiskClassifier,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit


class TestElevateOneTier:
    @pytest.mark.parametrize(
        ("level", "expected"),
        [
            (ApprovalRiskLevel.LOW, ApprovalRiskLevel.MEDIUM),
            (ApprovalRiskLevel.MEDIUM, ApprovalRiskLevel.HIGH),
            (ApprovalRiskLevel.HIGH, ApprovalRiskLevel.CRITICAL),
            (ApprovalRiskLevel.CRITICAL, ApprovalRiskLevel.CRITICAL),
        ],
    )
    def test_ladder(
        self,
        level: ApprovalRiskLevel,
        expected: ApprovalRiskLevel,
    ) -> None:
        assert elevate_one_tier(level) == expected


class TestWorkloadAdaptive:
    def test_below_threshold_passthrough(self) -> None:
        clf = WorkloadAdaptiveRiskClassifier(
            base=DefaultRiskTierClassifier(),
            inflight_probe=lambda: 3,
            threshold=10,
        )
        # code:read -> LOW in the default map; below threshold stays LOW.
        assert clf.classify("code:read") == ApprovalRiskLevel.LOW

    def test_at_threshold_elevates_one_tier(self) -> None:
        clf = WorkloadAdaptiveRiskClassifier(
            base=DefaultRiskTierClassifier(),
            inflight_probe=lambda: 10,
            threshold=10,
        )
        assert clf.classify("code:read") == ApprovalRiskLevel.MEDIUM

    def test_critical_stays_critical_under_load(self) -> None:
        clf = WorkloadAdaptiveRiskClassifier(
            base=DefaultRiskTierClassifier(),
            inflight_probe=lambda: 999,
            threshold=10,
        )
        assert clf.classify("deploy:production") == ApprovalRiskLevel.CRITICAL


class TestOperatorConfigurable:
    def test_mapped_action_returns_configured_tier(self) -> None:
        clf = OperatorConfigurableRiskClassifier(
            operator_map={"custom:thing": ApprovalRiskLevel.MEDIUM},
        )
        assert clf.classify("custom:thing") == ApprovalRiskLevel.MEDIUM

    def test_unknown_action_fails_safe_to_high(self) -> None:
        clf = OperatorConfigurableRiskClassifier(operator_map={})
        # D19: an operator taxonomy gap must never silently downgrade.
        assert clf.classify("unknown:action") == ApprovalRiskLevel.HIGH

    def test_caller_map_mutation_does_not_leak(self) -> None:
        live = {"a:b": ApprovalRiskLevel.LOW}
        clf = OperatorConfigurableRiskClassifier(operator_map=live)
        live["a:b"] = ApprovalRiskLevel.CRITICAL
        assert clf.classify("a:b") == ApprovalRiskLevel.LOW


class TestTimeBasedElevation:
    # 2026-01-01 is a Thursday; 2026-01-03 is a Saturday.
    def _clf(
        self,
        *,
        start: int = 20,
        end: int = 6,
        weekend: bool = True,
        now: datetime,
    ) -> TimeBasedRiskElevationClassifier:
        return TimeBasedRiskElevationClassifier(
            base=DefaultRiskTierClassifier(),
            off_hours_start_hour=start,
            off_hours_end_hour=end,
            weekend_elevation=weekend,
            clock=FakeClock(start=now),
        )

    def test_business_hours_weekday_passthrough(self) -> None:
        clf = self._clf(now=datetime(2026, 1, 1, 12, tzinfo=UTC))
        assert clf.classify("code:read") == ApprovalRiskLevel.LOW

    def test_off_hours_wrap_midnight_elevates(self) -> None:
        # 22:00 Thursday is inside the 20->6 wrapping window.
        clf = self._clf(now=datetime(2026, 1, 1, 22, tzinfo=UTC))
        assert clf.classify("code:read") == ApprovalRiskLevel.MEDIUM

    def test_early_morning_in_wrapped_window_elevates(self) -> None:
        clf = self._clf(now=datetime(2026, 1, 1, 3, tzinfo=UTC))
        assert clf.classify("code:read") == ApprovalRiskLevel.MEDIUM

    def test_weekend_elevates_regardless_of_hour(self) -> None:
        clf = self._clf(now=datetime(2026, 1, 3, 12, tzinfo=UTC))
        assert clf.classify("code:read") == ApprovalRiskLevel.MEDIUM

    def test_weekend_elevation_disabled(self) -> None:
        clf = self._clf(
            weekend=False,
            now=datetime(2026, 1, 3, 12, tzinfo=UTC),
        )
        assert clf.classify("code:read") == ApprovalRiskLevel.LOW


class TestFactory:
    def test_default_is_byte_identical(self) -> None:
        built = build_risk_tier_classifier(
            RiskClassifierConfig(),
            RiskClassifierDeps(),
        )
        baseline = DefaultRiskTierClassifier()
        for action in ("code:read", "deploy:production", "unknown:x"):
            assert built.classify(action) == baseline.classify(action)

    def test_operator_configurable_built(self) -> None:
        clf = build_risk_tier_classifier(
            RiskClassifierConfig(
                kind=RiskClassifierType.OPERATOR_CONFIGURABLE,
                operator_map={"x:y": ApprovalRiskLevel.MEDIUM},
            ),
            RiskClassifierDeps(),
        )
        assert clf.classify("x:y") == ApprovalRiskLevel.MEDIUM

    def test_workload_adaptive_requires_probe(self) -> None:
        with pytest.raises(RiskClassifierConfigError, match="inflight_probe"):
            build_risk_tier_classifier(
                RiskClassifierConfig(
                    kind=RiskClassifierType.WORKLOAD_ADAPTIVE,
                ),
                RiskClassifierDeps(),
            )

    def test_workload_adaptive_built_with_probe(self) -> None:
        clf = build_risk_tier_classifier(
            RiskClassifierConfig(
                kind=RiskClassifierType.WORKLOAD_ADAPTIVE,
                workload_threshold=1,
            ),
            RiskClassifierDeps(inflight_probe=lambda: 5),
        )
        assert clf.classify("code:read") == ApprovalRiskLevel.MEDIUM

    def test_time_based_built_with_clock(self) -> None:
        clf = build_risk_tier_classifier(
            RiskClassifierConfig(kind=RiskClassifierType.TIME_BASED),
            RiskClassifierDeps(
                clock=FakeClock(start=datetime(2026, 1, 1, 22, tzinfo=UTC)),
            ),
        )
        assert clf.classify("code:read") == ApprovalRiskLevel.MEDIUM

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(StrategyFactoryNotFoundError):
            build_risk_tier_classifier(
                RiskClassifierConfig.model_construct(kind="bogus"),  # type: ignore[arg-type]
                RiskClassifierDeps(),
            )


class TestTieredPolicyIntegration:
    def test_create_timeout_policy_uses_factory_classifier(self) -> None:
        config = TieredTimeoutConfig(
            risk_classifier=RiskClassifierConfig(
                kind=RiskClassifierType.OPERATOR_CONFIGURABLE,
                operator_map={"deploy:prod": ApprovalRiskLevel.CRITICAL},
            ),
        )
        policy = create_timeout_policy(config)
        assert isinstance(policy, TieredTimeoutPolicy)
        # The policy's classifier is the operator-configurable one:
        # an unmapped action fails safe to HIGH (D19), a mapped one
        # returns the operator tier.
        assert policy._classifier.classify("deploy:prod") == (
            ApprovalRiskLevel.CRITICAL
        )
        assert policy._classifier.classify("anything:else") == (ApprovalRiskLevel.HIGH)

    def test_default_tiered_config_byte_identical(self) -> None:
        policy = create_timeout_policy(TieredTimeoutConfig())
        assert isinstance(policy, TieredTimeoutPolicy)
        baseline = DefaultRiskTierClassifier()
        assert policy._classifier.classify("code:read") == baseline.classify(
            "code:read"
        )
