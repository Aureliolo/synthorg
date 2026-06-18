"""Tests for the shared security-rule utilities."""

from unittest.mock import MagicMock, patch

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.security.models import SecurityVerdictType
from synthorg.security.rules._utils import build_deny_verdict, walk_string_values


@pytest.mark.unit
class TestBuildDenyVerdict:
    """The shared DENY-verdict factory both detectors delegate to."""

    def test_builds_deny_with_matched_rule(self) -> None:
        verdict = build_deny_verdict(
            reason="credential detected",
            risk_level=ApprovalRiskLevel.HIGH,
            rule_name="aws_secret_key",
        )
        assert verdict.verdict is SecurityVerdictType.DENY
        assert verdict.reason == "credential detected"
        assert verdict.risk_level is ApprovalRiskLevel.HIGH
        assert verdict.matched_rules == ("aws_secret_key",)

    def test_timing_is_placeholder_zero(self) -> None:
        verdict = build_deny_verdict(
            reason="x",
            risk_level=ApprovalRiskLevel.LOW,
            rule_name="r",
        )
        # The engine overwrites timing downstream; the factory stamps 0.0.
        assert verdict.evaluation_duration_ms == 0.0

    def test_evaluated_at_is_timezone_aware(self) -> None:
        verdict = build_deny_verdict(
            reason="x",
            risk_level=ApprovalRiskLevel.LOW,
            rule_name="r",
        )
        assert verdict.evaluated_at.tzinfo is not None


@pytest.mark.unit
class TestWalkStringValuesFlat:
    """Flat dict inputs."""

    def test_empty_dict(self) -> None:
        assert list(walk_string_values({})) == []

    def test_single_string_value(self) -> None:
        result = list(walk_string_values({"a": "hello"}))
        assert result == ["hello"]

    def test_multiple_string_values(self) -> None:
        result = list(walk_string_values({"a": "x", "b": "y"}))
        assert set(result) == {"x", "y"}

    def test_non_string_values_skipped(self) -> None:
        result = list(walk_string_values({"a": 42, "b": None, "c": True}))
        assert result == []

    def test_mixed_types(self) -> None:
        result = list(
            walk_string_values({"a": "found", "b": 42, "c": "also"}),
        )
        assert set(result) == {"found", "also"}


@pytest.mark.unit
class TestWalkStringValuesNested:
    """Nested dict and list inputs."""

    def test_nested_dict(self) -> None:
        data: dict[str, object] = {"outer": {"inner": "deep"}}
        result = list(walk_string_values(data))
        assert result == ["deep"]

    def test_nested_list(self) -> None:
        data: dict[str, object] = {"items": ["one", "two", "three"]}
        result = list(walk_string_values(data))
        assert result == ["one", "two", "three"]

    def test_list_of_dicts(self) -> None:
        data: dict[str, object] = {
            "entries": [{"name": "alice"}, {"name": "bob"}],
        }
        result = list(walk_string_values(data))
        assert set(result) == {"alice", "bob"}

    def test_deeply_nested(self) -> None:
        data: dict[str, object] = {"a": {"b": {"c": [{"d": "found"}]}}}
        result = list(walk_string_values(data))
        assert result == ["found"]

    def test_list_with_mixed_types(self) -> None:
        data: dict[str, object] = {
            "items": ["text", 42, None, {"key": "nested"}, True],
        }
        result = list(walk_string_values(data))
        assert set(result) == {"text", "nested"}

    def test_nested_lists(self) -> None:
        data: dict[str, object] = {"items": [["a", "b"], ["c"]]}
        result = list(walk_string_values(data))
        assert set(result) == {"a", "b", "c"}


@pytest.mark.unit
class TestWalkStringValuesDepthLimit:
    """Depth limit prevents infinite recursion."""

    @patch("synthorg.security.rules._utils.logger")
    def test_stops_at_max_depth(self, mock_logger: MagicMock) -> None:
        """Build a structure deeper than 20 levels -- truncated with warning."""
        data: dict[str, object] = {"val": "leaf"}
        for _ in range(25):
            data = {"nested": data}

        result = list(walk_string_values(data))

        # "leaf" is beyond depth limit and should be skipped.
        assert result == []
        # Logger.warning should have been called with the depth event.
        mock_logger.warning.assert_called()
        call_kwargs = mock_logger.warning.call_args
        assert call_kwargs.kwargs.get("depth") is not None

    def test_list_recursion_respects_depth_limit(self) -> None:
        """Deeply nested lists stop at max depth without RecursionError."""
        # Build a 25-level nested list structure (no dicts).
        inner: object = "leaf"
        for _ in range(25):
            inner = [inner]
        data: dict[str, object] = {"items": inner}

        result = list(walk_string_values(data))
        # "leaf" is beyond depth limit and should be skipped.
        assert result == []
