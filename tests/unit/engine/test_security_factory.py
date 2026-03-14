"""Tests for _security_factory module."""

from unittest.mock import MagicMock

import pytest

from ai_company.engine._security_factory import (
    make_security_interceptor,
    registry_with_approval_tool,
)
from ai_company.engine.errors import ExecutionStateError

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def _make_audit_log() -> MagicMock:
    return MagicMock()


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
        from ai_company.security.config import (
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
        from ai_company.security.config import (
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


class TestRegistryWithApprovalTool:
    """registry_with_approval_tool() factory function."""

    def test_returns_original_when_no_store(self) -> None:
        registry = MagicMock()
        result = registry_with_approval_tool(
            registry,
            None,
            MagicMock(id="agent-1"),
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
