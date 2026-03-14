"""Tests for the policy validator security rule."""

import pytest

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.security.models import SecurityContext, SecurityVerdictType
from synthorg.security.rules.policy_validator import PolicyValidator

pytestmark = pytest.mark.timeout(30)

# Default policy lists matching SecurityConfig defaults.
_HARD_DENY: frozenset[str] = frozenset(
    {"deploy:production", "db:admin", "org:fire"},
)
_AUTO_APPROVE: frozenset[str] = frozenset({"code:read", "docs:write"})


def _ctx(
    *,
    action_type: str,
    arguments: dict[str, object] | None = None,
) -> SecurityContext:
    """Build a SecurityContext with sensible defaults."""
    return SecurityContext(
        tool_name="test-tool",
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type=action_type,
        arguments=arguments or {},
    )


def _validator(
    *,
    hard_deny: frozenset[str] = _HARD_DENY,
    auto_approve: frozenset[str] = _AUTO_APPROVE,
) -> PolicyValidator:
    """Build a PolicyValidator with default or custom policy lists."""
    return PolicyValidator(
        hard_deny_action_types=hard_deny,
        auto_approve_action_types=auto_approve,
    )


# ── Hard-deny action types ───────────────────────────────────────────


@pytest.mark.unit
class TestPolicyValidatorHardDeny:
    """Action types in the hard-deny list produce DENY verdicts."""

    @pytest.mark.parametrize(
        "action_type",
        ["deploy:production", "db:admin", "org:fire"],
    )
    def test_hard_deny_action_returns_deny(
        self,
        action_type: str,
    ) -> None:
        """Hard-deny action types are always denied."""
        validator = _validator()
        ctx = _ctx(action_type=action_type)
        verdict = validator.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY
        assert verdict.risk_level == ApprovalRiskLevel.CRITICAL
        assert "policy_validator" in verdict.matched_rules
        assert "hard-deny" in verdict.reason

    def test_hard_deny_takes_priority_over_auto_approve(self) -> None:
        """If an action type is in both lists, deny wins."""
        overlap_type = "special:action"
        validator = _validator(
            hard_deny=frozenset({overlap_type}),
            auto_approve=frozenset({overlap_type}),
        )
        ctx = _ctx(action_type=overlap_type)
        verdict = validator.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY


# ── Auto-approve action types ────────────────────────────────────────


@pytest.mark.unit
class TestPolicyValidatorAutoApprove:
    """Action types in the auto-approve list produce ALLOW verdicts."""

    @pytest.mark.parametrize(
        "action_type",
        ["code:read", "docs:write"],
    )
    def test_auto_approve_action_returns_allow(
        self,
        action_type: str,
    ) -> None:
        """Auto-approve action types are always allowed."""
        validator = _validator()
        ctx = _ctx(action_type=action_type)
        verdict = validator.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.ALLOW
        assert verdict.risk_level == ApprovalRiskLevel.LOW
        assert "policy_validator" in verdict.matched_rules
        assert "auto-approve" in verdict.reason


# ── Pass-through (neither list) ──────────────────────────────────────


@pytest.mark.unit
class TestPolicyValidatorPassThrough:
    """Action types in neither list return None."""

    @pytest.mark.parametrize(
        "action_type",
        [
            "code:write",
            "test:run",
            "vcs:push",
            "comms:internal",
            "unknown:action",
        ],
    )
    def test_returns_none_for_unlisted_action(
        self,
        action_type: str,
    ) -> None:
        """Action types not in either list produce no verdict."""
        validator = _validator()
        ctx = _ctx(action_type=action_type)
        assert validator.evaluate(ctx) is None


# ── Empty policy lists ───────────────────────────────────────────────


@pytest.mark.unit
class TestPolicyValidatorEmptyLists:
    """With empty deny/approve lists, everything passes through."""

    def test_empty_lists_return_none(self) -> None:
        """Empty policy lists produce no verdict for any action."""
        validator = _validator(
            hard_deny=frozenset(),
            auto_approve=frozenset(),
        )
        ctx = _ctx(action_type="deploy:production")
        assert validator.evaluate(ctx) is None


# ── Name property ────────────────────────────────────────────────────


@pytest.mark.unit
class TestPolicyValidatorName:
    """Verify the rule name property."""

    def test_name_is_policy_validator(self) -> None:
        validator = _validator()
        assert validator.name == "policy_validator"
