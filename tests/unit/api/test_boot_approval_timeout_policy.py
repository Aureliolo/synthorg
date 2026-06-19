"""The boot approval-timeout scheduler resolves its policy from config.

``build_default_approval_timeout_scheduler`` previously hardwired
:class:`WaitForeverPolicy`. It now builds the policy from the resolved
``config.approval_timeout`` company-template field via
:func:`create_timeout_policy`, falling back to the wait-forever default
when the config is absent or maps to an unrecognised type.
"""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.boot_resolvers import (
    build_default_approval_timeout_scheduler,
)
from synthorg.security.timeout.config import (
    DenyOnTimeoutConfig,
    WaitForeverConfig,
)
from synthorg.security.timeout.policies import (
    DenyOnTimeoutPolicy,
    WaitForeverPolicy,
)

pytestmark = pytest.mark.unit


class TestBootApprovalTimeoutPolicy:
    def test_none_config_falls_back_to_wait_forever(self) -> None:
        scheduler = build_default_approval_timeout_scheduler(
            approval_store=ApprovalStore(),
        )
        assert isinstance(scheduler._checker._policy, WaitForeverPolicy)

    def test_wait_config_builds_wait_forever_policy(self) -> None:
        scheduler = build_default_approval_timeout_scheduler(
            approval_store=ApprovalStore(),
            approval_timeout_config=WaitForeverConfig(),
        )
        assert isinstance(scheduler._checker._policy, WaitForeverPolicy)

    def test_deny_config_builds_deny_on_timeout_policy(self) -> None:
        scheduler = build_default_approval_timeout_scheduler(
            approval_store=ApprovalStore(),
            approval_timeout_config=DenyOnTimeoutConfig(timeout_minutes=30.0),
        )
        policy = scheduler._checker._policy
        assert isinstance(policy, DenyOnTimeoutPolicy)
        # 30 minutes propagates through the builder as seconds.
        assert policy._timeout_seconds == 30.0 * 60
