"""Tests for CedarPolicyEngine."""

from unittest.mock import patch

import cedarpy
import pytest

from synthorg.security.policy_engine.cedar_engine import CedarPolicyEngine
from synthorg.security.policy_engine.models import PolicyActionRequest


def _make_request(
    action_type: str = "tool_invoke",
    principal: str = "agent-001",
    resource: str = "read_file",
) -> PolicyActionRequest:
    return PolicyActionRequest(
        action_type=action_type,
        principal=principal,
        resource=resource,
        context={"task_id": "t-1"},
    )


@pytest.mark.unit
class TestCedarPolicyEngine:
    """Tests for CedarPolicyEngine evaluation."""

    async def test_name_property(self) -> None:
        engine = CedarPolicyEngine(
            policy_texts=("permit(principal, action, resource);",),
        )
        assert engine.name == "cedar"

    async def test_allow_decision(self) -> None:
        engine = CedarPolicyEngine(
            policy_texts=("permit(principal, action, resource);",),
        )
        decision = await engine.evaluate(_make_request())
        assert decision.allow is True
        assert decision.latency_ms >= 0

    async def test_deny_when_no_matching_policy(self) -> None:
        # With a forbid-all policy, should deny.
        engine = CedarPolicyEngine(
            policy_texts=("forbid(principal, action, resource);",),
        )
        decision = await engine.evaluate(_make_request())
        assert decision.allow is False

    async def test_latency_recorded(self) -> None:
        engine = CedarPolicyEngine(
            policy_texts=("permit(principal, action, resource);",),
        )
        decision = await engine.evaluate(_make_request())
        assert isinstance(decision.latency_ms, float)
        assert decision.latency_ms >= 0

    async def test_policy_set_pre_parsed(self) -> None:
        """The policy text is parsed once at construction, not per call.

        Guards against a regression back to passing a raw policy
        string to ``cedarpy.is_authorized`` on every ``evaluate()``.
        """
        engine = CedarPolicyEngine(
            policy_texts=("permit(principal, action, resource);",),
        )
        cedar_request = {
            "principal": 'Principal::"agent-001"',
            "action": 'Action::"tool_invoke"',
            "resource": 'Resource::"read_file"',
            "context": {},
        }
        result = cedarpy.is_authorized(cedar_request, engine._policy_set, [])
        assert result.metrics["policies_pre_parsed"] == 1

    async def test_fail_open_on_error(self) -> None:
        """When fail_closed=False, errors return allow."""
        engine = CedarPolicyEngine(
            policy_texts=("permit(principal, action, resource);",),
            fail_closed=False,
        )
        # Patch is_authorized to raise.
        with patch(
            "synthorg.security.policy_engine.cedar_engine.cedarpy",
        ) as mock_cedar:
            mock_cedar.is_authorized.side_effect = RuntimeError("boom")
            decision = await engine.evaluate(_make_request())
        assert decision.allow is True
        assert "error" in decision.reason.lower()

    async def test_fail_closed_on_error(self) -> None:
        """When fail_closed=True, errors return deny."""
        engine = CedarPolicyEngine(
            policy_texts=("permit(principal, action, resource);",),
            fail_closed=True,
        )
        with patch(
            "synthorg.security.policy_engine.cedar_engine.cedarpy",
        ) as mock_cedar:
            mock_cedar.is_authorized.side_effect = RuntimeError("boom")
            decision = await engine.evaluate(_make_request())
        assert decision.allow is False
        assert "error" in decision.reason.lower()
