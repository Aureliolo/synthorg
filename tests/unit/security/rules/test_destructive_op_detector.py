"""Tests for the destructive operation detector security rule."""

import pytest

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.security.models import SecurityContext, SecurityVerdictType
from synthorg.security.rules.destructive_op_detector import (
    DestructiveOpDetector,
)

pytestmark = pytest.mark.timeout(30)


def _ctx(
    arguments: dict[str, object] | None = None,
    *,
    action_type: str = "code:write",
) -> SecurityContext:
    """Build a SecurityContext with sensible defaults."""
    return SecurityContext(
        tool_name="test-tool",
        tool_category=ToolCategory.TERMINAL,
        action_type=action_type,
        arguments=arguments or {},
    )


# ── Detection of destructive patterns ────────────────────────────────


@pytest.mark.unit
class TestDestructiveOpDetectorDenyPatterns:
    """Operations that result in DENY verdicts."""

    @pytest.mark.parametrize(
        ("label", "command"),
        [
            ("rm -rf /", "rm -rf /"),
            ("rm -rf with path", "rm -rf /home/user/project"),
            ("rm -fr (reversed flags)", "rm -fr /tmp/data"),
            ("DROP DATABASE", "DROP DATABASE production;"),
            ("drop database (lowercase)", "drop database test;"),
            ("mkfs", "mkfs.ext4 /dev/sda1"),
            ("format", "format C:"),
        ],
    )
    def test_deny_patterns_produce_deny_verdict(
        self,
        label: str,
        command: str,
    ) -> None:
        """Hard-deny destructive operations produce DENY verdicts."""
        detector = DestructiveOpDetector()
        ctx = _ctx({"command": command})
        verdict = detector.evaluate(ctx)

        assert verdict is not None, f"Expected detection of: {label}"
        assert verdict.verdict == SecurityVerdictType.DENY
        assert verdict.risk_level == ApprovalRiskLevel.CRITICAL
        assert "destructive_op_detector" in verdict.matched_rules


@pytest.mark.unit
class TestDestructiveOpDetectorEscalatePatterns:
    """Operations that result in ESCALATE verdicts."""

    @pytest.mark.parametrize(
        ("label", "command"),
        [
            ("DROP TABLE", "DROP TABLE users;"),
            ("drop table (lowercase)", "drop table orders;"),
            (
                "DELETE without WHERE",
                "DELETE FROM users;",
            ),
            ("TRUNCATE TABLE", "TRUNCATE TABLE sessions;"),
            ("git push --force", "git push --force origin main"),
            ("git reset --hard", "git reset --hard HEAD~1"),
        ],
    )
    def test_escalate_patterns_produce_escalate_verdict(
        self,
        label: str,
        command: str,
    ) -> None:
        """Recoverable destructive operations produce ESCALATE verdicts."""
        detector = DestructiveOpDetector()
        ctx = _ctx({"command": command})
        verdict = detector.evaluate(ctx)

        assert verdict is not None, f"Expected detection of: {label}"
        assert verdict.verdict == SecurityVerdictType.ESCALATE
        assert verdict.risk_level == ApprovalRiskLevel.HIGH


# ── Severity ordering ────────────────────────────────────────────────


@pytest.mark.unit
class TestDestructiveOpDetectorSeverityOrdering:
    """When multiple patterns match, DENY takes priority over ESCALATE."""

    def test_deny_overrides_escalate(self) -> None:
        """Mixed deny+escalate findings result in DENY."""
        detector = DestructiveOpDetector()
        ctx = _ctx(
            {
                "step1": "DROP TABLE users;",
                "step2": "rm -rf /var/data",
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_multiple_escalate_patterns_stay_escalate(self) -> None:
        """Multiple escalate-only findings remain ESCALATE."""
        detector = DestructiveOpDetector()
        ctx = _ctx(
            {
                "a": "DROP TABLE users;",
                "b": "git push --force origin main",
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.ESCALATE


# ── Nested and list scanning ─────────────────────────────────────────


@pytest.mark.unit
class TestDestructiveOpDetectorNestedScanning:
    """Destructive patterns in nested structures are detected."""

    def test_detects_in_nested_dict(self) -> None:
        """Patterns in nested dicts are caught."""
        detector = DestructiveOpDetector()
        ctx = _ctx({"outer": {"inner": "rm -rf /tmp"}})
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_detects_in_list(self) -> None:
        """Patterns in list values are caught."""
        detector = DestructiveOpDetector()
        ctx = _ctx({"commands": ["ls -la", "rm -rf /data"]})
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_detects_in_list_of_dicts(self) -> None:
        """Patterns in dicts inside lists are caught."""
        detector = DestructiveOpDetector()
        ctx = _ctx(
            {"steps": [{"cmd": "DROP DATABASE staging;"}]},
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY


# ── Clean input (no detection) ───────────────────────────────────────


@pytest.mark.unit
class TestDestructiveOpDetectorPassThrough:
    """Clean inputs return None (no verdict)."""

    @pytest.mark.parametrize(
        "arguments",
        [
            {},
            {"command": "ls -la"},
            {"command": "git push origin main"},
            {"command": "git reset --soft HEAD~1"},
            {"query": "SELECT * FROM users WHERE id = 1"},
            {"query": "DELETE FROM sessions WHERE expired = true;"},
            {"command": "rm temp.txt"},
            {"command": "mkdir -p /app/data"},
        ],
        ids=[
            "empty",
            "ls_command",
            "git_push_no_force",
            "git_reset_soft",
            "select_query",
            "delete_with_where",
            "rm_without_rf",
            "mkdir",
        ],
    )
    def test_returns_none_for_clean_input(
        self,
        arguments: dict[str, object],
    ) -> None:
        """Non-destructive arguments produce no verdict."""
        detector = DestructiveOpDetector()
        ctx = _ctx(arguments)
        assert detector.evaluate(ctx) is None


# ── Name property ────────────────────────────────────────────────────


@pytest.mark.unit
class TestDestructiveOpDetectorName:
    """Verify the rule name property."""

    def test_name_is_destructive_op_detector(self) -> None:
        detector = DestructiveOpDetector()
        assert detector.name == "destructive_op_detector"
