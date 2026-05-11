"""Tests for _security_factory module."""

from unittest.mock import MagicMock

import pytest

from synthorg.engine._security_factory import (
    make_security_interceptor,
    registry_with_approval_tool,
)
from synthorg.engine.errors import ExecutionStateError

pytestmark = pytest.mark.unit


def _make_audit_log() -> MagicMock:
    from synthorg.security.audit import AuditLog

    return MagicMock(spec=AuditLog)


def _make_security_config(*, enabled: bool = True) -> MagicMock:
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.hard_deny_action_types = []
    cfg.auto_approve_action_types = []
    re_cfg = MagicMock()
    re_cfg.credential_patterns_enabled = False
    re_cfg.path_traversal_detection_enabled = False
    re_cfg.destructive_op_detection_enabled = False
    re_cfg.data_leak_detection_enabled = False
    cfg.rule_engine = re_cfg
    return cfg


class TestMakeSecurityInterceptor:
    """make_security_interceptor() factory function."""

    def test_returns_none_when_no_config(self) -> None:
        result = make_security_interceptor(None, _make_audit_log())
        assert result is None

    def test_returns_none_when_disabled(self) -> None:
        cfg = _make_security_config(enabled=False)
        result = make_security_interceptor(cfg, _make_audit_log())
        assert result is None

    def test_raises_when_no_config_but_autonomy_set(self) -> None:
        autonomy = MagicMock()
        with pytest.raises(ExecutionStateError, match="effective_autonomy"):
            make_security_interceptor(
                None,
                _make_audit_log(),
                effective_autonomy=autonomy,
            )

    def test_raises_when_disabled_but_autonomy_set(self) -> None:
        cfg = _make_security_config(enabled=False)
        autonomy = MagicMock()
        with pytest.raises(ExecutionStateError, match="effective_autonomy"):
            make_security_interceptor(
                cfg,
                _make_audit_log(),
                effective_autonomy=autonomy,
            )

    def test_returns_interceptor_when_enabled(self) -> None:
        from synthorg.security.config import (
            RuleEngineConfig,
            SecurityConfig,
        )

        cfg = SecurityConfig(
            enabled=True,
            rule_engine=RuleEngineConfig(),
        )
        result = make_security_interceptor(cfg, _make_audit_log())
        assert result is not None

    def test_returns_interceptor_with_all_detectors(self) -> None:
        from synthorg.security.config import (
            RuleEngineConfig,
            SecurityConfig,
        )

        cfg = SecurityConfig(
            enabled=True,
            rule_engine=RuleEngineConfig(
                credential_patterns_enabled=True,
                path_traversal_detection_enabled=True,
                destructive_op_detection_enabled=True,
                data_leak_detection_enabled=True,
            ),
        )
        result = make_security_interceptor(cfg, _make_audit_log())
        assert result is not None


class TestCustomPolicyWiring:
    """Custom policies are wired into the rule engine pipeline."""

    def test_custom_policies_included_in_rules(self) -> None:
        from synthorg.security.config import (
            RuleEngineConfig,
            SecurityConfig,
            SecurityPolicyRule,
        )
        from synthorg.security.rules.custom_policy_rule import (
            CustomPolicyRule,
        )

        policy = SecurityPolicyRule(
            name="block-deploy",
            action_types=("deploy:staging",),
        )
        cfg = SecurityConfig(
            enabled=True,
            rule_engine=RuleEngineConfig(),
            custom_policies=(policy,),
        )
        svc = make_security_interceptor(cfg, _make_audit_log())

        assert svc is not None
        engine = svc._rule_engine  # type: ignore[attr-defined]
        custom_rules = [r for r in engine._rules if isinstance(r, CustomPolicyRule)]
        assert len(custom_rules) == 1
        assert custom_rules[0].name == "custom_policy:block-deploy"

    def test_disabled_custom_policies_excluded(self) -> None:
        from synthorg.security.config import (
            RuleEngineConfig,
            SecurityConfig,
            SecurityPolicyRule,
        )
        from synthorg.security.rules.custom_policy_rule import (
            CustomPolicyRule,
        )

        policy = SecurityPolicyRule(
            name="disabled-rule",
            action_types=("code:write",),
            enabled=False,
        )
        cfg = SecurityConfig(
            enabled=True,
            rule_engine=RuleEngineConfig(),
            custom_policies=(policy,),
        )
        svc = make_security_interceptor(cfg, _make_audit_log())

        assert svc is not None
        engine = svc._rule_engine  # type: ignore[attr-defined]
        custom_rules = [r for r in engine._rules if isinstance(r, CustomPolicyRule)]
        assert len(custom_rules) == 0

    def test_custom_policies_after_detectors_by_default(self) -> None:
        from synthorg.security.config import (
            RuleEngineConfig,
            SecurityConfig,
            SecurityPolicyRule,
        )
        from synthorg.security.rules.credential_detector import (
            CredentialDetector,
        )
        from synthorg.security.rules.custom_policy_rule import (
            CustomPolicyRule,
        )

        policy = SecurityPolicyRule(
            name="my-rule",
            action_types=("code:write",),
        )
        cfg = SecurityConfig(
            enabled=True,
            rule_engine=RuleEngineConfig(
                credential_patterns_enabled=True,
            ),
            custom_policies=(policy,),
        )
        svc = make_security_interceptor(cfg, _make_audit_log())

        assert svc is not None
        engine = svc._rule_engine  # type: ignore[attr-defined]
        rules = engine._rules

        # Find positions
        detector_idx = next(
            i for i, r in enumerate(rules) if isinstance(r, CredentialDetector)
        )
        custom_idx = next(
            i for i, r in enumerate(rules) if isinstance(r, CustomPolicyRule)
        )
        assert custom_idx > detector_idx

    def test_custom_policies_before_detectors_when_bypass(self) -> None:
        from synthorg.security.config import (
            RuleEngineConfig,
            SecurityConfig,
            SecurityPolicyRule,
        )
        from synthorg.security.rules.credential_detector import (
            CredentialDetector,
        )
        from synthorg.security.rules.custom_policy_rule import (
            CustomPolicyRule,
        )

        policy = SecurityPolicyRule(
            name="early-rule",
            action_types=("code:write",),
        )
        cfg = SecurityConfig(
            enabled=True,
            rule_engine=RuleEngineConfig(
                credential_patterns_enabled=True,
                custom_allow_bypasses_detectors=True,
            ),
            custom_policies=(policy,),
        )
        svc = make_security_interceptor(cfg, _make_audit_log())

        assert svc is not None
        engine = svc._rule_engine  # type: ignore[attr-defined]
        rules = engine._rules

        detector_idx = next(
            i for i, r in enumerate(rules) if isinstance(r, CredentialDetector)
        )
        custom_idx = next(
            i for i, r in enumerate(rules) if isinstance(r, CustomPolicyRule)
        )
        assert custom_idx < detector_idx

    def test_mixed_enabled_disabled_custom_policies(self) -> None:
        from synthorg.security.config import (
            RuleEngineConfig,
            SecurityConfig,
            SecurityPolicyRule,
        )
        from synthorg.security.rules.custom_policy_rule import (
            CustomPolicyRule,
        )

        policies = (
            SecurityPolicyRule(
                name="enabled-1",
                action_types=("code:write",),
            ),
            SecurityPolicyRule(
                name="disabled-1",
                action_types=("code:read",),
                enabled=False,
            ),
            SecurityPolicyRule(
                name="enabled-2",
                action_types=("vcs:push",),
            ),
        )
        cfg = SecurityConfig(
            enabled=True,
            rule_engine=RuleEngineConfig(),
            custom_policies=policies,
        )
        svc = make_security_interceptor(cfg, _make_audit_log())

        assert svc is not None
        engine = svc._rule_engine  # type: ignore[attr-defined]
        custom_rules = [r for r in engine._rules if isinstance(r, CustomPolicyRule)]
        assert len(custom_rules) == 2
        names = {r.name for r in custom_rules}
        assert names == {"custom_policy:enabled-1", "custom_policy:enabled-2"}


class TestRegistryWithApprovalTool:
    """registry_with_approval_tool() factory function."""

    def test_returns_original_when_no_store(self) -> None:
        from synthorg.core.agent import AgentIdentity

        registry = MagicMock()
        result = registry_with_approval_tool(
            registry,
            None,
            MagicMock(spec=AgentIdentity, id="agent-1"),
        )
        assert result is registry

    def test_returns_new_registry_with_store(self) -> None:
        registry = MagicMock()
        registry.all_tools.return_value = []
        store = MagicMock()
        identity = MagicMock()
        identity.id = "agent-1"

        result = registry_with_approval_tool(
            registry,
            store,
            identity,
            task_id="task-1",
        )
        assert result is not registry
