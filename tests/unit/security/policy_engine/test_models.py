"""Tests for PolicyEngine models."""

from collections.abc import Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from synthorg.security.policy_engine.models import (
    PolicyActionRequest,
    PolicyDecision,
)


@pytest.mark.unit
class TestPolicyActionRequest:
    """Tests for PolicyActionRequest validation."""

    def test_basic_construction(self) -> None:
        req = PolicyActionRequest(
            action_type="tool_invoke",
            principal="agent-001",
            resource="read_file",
            context={"task_id": "t-1"},
        )
        assert req.action_type == "tool_invoke"
        assert req.principal == "agent-001"
        assert req.resource == "read_file"

    def test_frozen(self) -> None:
        req = PolicyActionRequest(
            action_type="tool_invoke",
            principal="agent-001",
            resource="read_file",
        )
        with pytest.raises(ValidationError):
            req.action_type = "delegation"  # type: ignore[misc]

    def test_blank_action_type_rejected(self) -> None:
        with pytest.raises(
            ValueError, match=r"at least 1 character|must not be blank|whitespace-only"
        ):
            PolicyActionRequest(
                action_type="   ",
                principal="agent-001",
                resource="read_file",
            )

    def test_blank_principal_rejected(self) -> None:
        with pytest.raises(
            ValueError, match=r"at least 1 character|must not be blank|whitespace-only"
        ):
            PolicyActionRequest(
                action_type="tool_invoke",
                principal="",
                resource="read_file",
            )

    def test_context_deep_copied(self) -> None:
        original_ctx = {"nested": {"key": "value"}}
        req = PolicyActionRequest(
            action_type="tool_invoke",
            principal="agent-001",
            resource="read_file",
            context=original_ctx,
        )
        # Mutate original -- should not affect the request.
        original_ctx["nested"]["key"] = "mutated"
        nested = req.context["nested"]
        assert isinstance(nested, Mapping)
        assert nested["key"] == "value"

    def test_default_context_is_empty(self) -> None:
        req = PolicyActionRequest(
            action_type="tool_invoke",
            principal="agent-001",
            resource="read_file",
        )
        assert req.context == {}


@pytest.mark.unit
class TestPolicyDecision:
    """Tests for PolicyDecision validation."""

    def test_allow_decision(self) -> None:
        decision = PolicyDecision(
            allow=True,
            reason="Policy permits this action",
            matched_policy="allow-read-files",
            latency_ms=0.5,
        )
        assert decision.allow is True
        assert decision.matched_policy == "allow-read-files"

    def test_deny_decision(self) -> None:
        decision = PolicyDecision(
            allow=False,
            reason="Policy denies code execution",
            latency_ms=1.2,
        )
        assert decision.allow is False
        assert decision.matched_policy is None

    def test_frozen(self) -> None:
        decision = PolicyDecision(
            allow=True,
            reason="ok",
            latency_ms=0.1,
        )
        with pytest.raises(ValidationError):
            decision.allow = False  # type: ignore[misc]

    def test_blank_reason_rejected(self) -> None:
        with pytest.raises(
            ValueError, match=r"at least 1 character|must not be blank|whitespace-only"
        ):
            PolicyDecision(
                allow=True,
                reason="",
                latency_ms=0.1,
            )


@pytest.mark.unit
class TestPolicyActionRequestProperties:
    """Property-based tests for PolicyActionRequest."""

    @given(
        action_type=st.text(
            alphabet=st.characters(categories=("L", "N")),
            min_size=1,
            max_size=50,
        ),
        principal=st.text(
            alphabet=st.characters(categories=("L", "N")),
            min_size=1,
            max_size=50,
        ),
        resource=st.text(
            alphabet=st.characters(categories=("L", "N")),
            min_size=1,
            max_size=50,
        ),
    )
    def test_always_constructs_valid_request(
        self,
        action_type: str,
        principal: str,
        resource: str,
    ) -> None:
        req = PolicyActionRequest(
            action_type=action_type,
            principal=principal,
            resource=resource,
        )
        assert req.action_type == action_type
        assert req.principal == principal
        assert req.resource == resource
