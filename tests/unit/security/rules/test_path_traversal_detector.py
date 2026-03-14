"""Tests for the path traversal detector security rule."""

import pytest

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.security.models import SecurityContext, SecurityVerdictType
from synthorg.security.rules.path_traversal_detector import (
    PathTraversalDetector,
)

pytestmark = pytest.mark.timeout(30)


def _ctx(
    arguments: dict[str, object] | None = None,
    *,
    action_type: str = "code:read",
) -> SecurityContext:
    """Build a SecurityContext with sensible defaults."""
    return SecurityContext(
        tool_name="test-tool",
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type=action_type,
        arguments=arguments or {},
    )


# ── Detection of traversal patterns ──────────────────────────────────


@pytest.mark.unit
class TestPathTraversalDetectorPatterns:
    """Path traversal detector catches known traversal patterns."""

    @pytest.mark.parametrize(
        ("label", "value"),
        [
            ("../ at start", "../etc/passwd"),
            ("../ in middle", "/app/../../../etc/shadow"),
            ("../ with backslash", "..\\windows\\system32"),
            ("../ backslash in middle", "C:\\app\\..\\..\\secrets"),
            ("null byte injection", "/app/file\x00.txt"),
            ("URL-encoded ../ (%2e%2e/)", "%2e%2e/etc/passwd"),
            ("URL-encoded (%2e%2e%2f)", "%2e%2e%2fetc/passwd"),
            ("URL-encoded mixed case", "%2E%2E/etc/passwd"),
            ("URL-encoded after slash", "/../%2e%2e/etc/passwd"),
            ("double-encoded traversal", "%252e%252e/etc/passwd"),
            ("double-encoded mixed case", "%252E%252E/etc/shadow"),
        ],
    )
    def test_detects_traversal_pattern(
        self,
        label: str,
        value: str,
    ) -> None:
        """Each traversal pattern triggers a DENY verdict."""
        detector = PathTraversalDetector()
        ctx = _ctx({"path": value})
        verdict = detector.evaluate(ctx)

        assert verdict is not None, f"Expected detection of: {label}"
        assert verdict.verdict == SecurityVerdictType.DENY
        assert verdict.risk_level == ApprovalRiskLevel.CRITICAL
        assert "path_traversal_detector" in verdict.matched_rules

    def test_detects_traversal_in_nested_dict(self) -> None:
        """Traversal patterns in nested dicts are caught."""
        detector = PathTraversalDetector()
        ctx = _ctx({"config": {"path": "../../etc/passwd"}})
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_detects_traversal_in_list(self) -> None:
        """Traversal patterns in list values are caught."""
        detector = PathTraversalDetector()
        ctx = _ctx({"paths": ["/safe/path", "../../../etc/shadow"]})
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_detects_traversal_in_list_of_dicts(self) -> None:
        """Traversal patterns in dicts inside lists are caught."""
        detector = PathTraversalDetector()
        ctx = _ctx({"entries": [{"file": "../../secret"}]})
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_multiple_findings_deduped(self) -> None:
        """Multiple traversal types produce deduplicated findings."""
        detector = PathTraversalDetector()
        ctx = _ctx(
            {
                "a": "../etc/passwd",
                "b": "/app/file\x00.txt",
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert "directory traversal" in verdict.reason
        assert "null byte" in verdict.reason


# ── Clean input (no detection) ───────────────────────────────────────


@pytest.mark.unit
class TestPathTraversalDetectorPassThrough:
    """Clean inputs return None (no verdict)."""

    @pytest.mark.parametrize(
        "arguments",
        [
            {},
            {"path": "/app/src/main.py"},
            {"path": "relative/path/file.txt"},
            {"path": "/home/user/project/README.md"},
            {"content": "The .. operator is used for ranges"},
            {"data": ["file1.txt", "file2.txt"]},
            {"path": "/app/.hidden/config"},
        ],
        ids=[
            "empty",
            "absolute_path",
            "relative_no_traversal",
            "normal_home_path",
            "dots_in_text_no_slash",
            "safe_list",
            "hidden_dir",
        ],
    )
    def test_returns_none_for_clean_input(
        self,
        arguments: dict[str, object],
    ) -> None:
        """Non-traversal arguments produce no verdict."""
        detector = PathTraversalDetector()
        ctx = _ctx(arguments)
        assert detector.evaluate(ctx) is None


# ── Name property ────────────────────────────────────────────────────


@pytest.mark.unit
class TestPathTraversalDetectorName:
    """Verify the rule name property."""

    def test_name_is_path_traversal_detector(self) -> None:
        detector = PathTraversalDetector()
        assert detector.name == "path_traversal_detector"
