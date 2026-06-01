"""Tests for ToolRegistryIntegrityChecker."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import JsonValue, ValidationError

from synthorg.tools.integrity_check import (
    ToolIntegrityCheckConfig,
    ToolIntegrityChecker,
    ToolIntegrityReport,
    ToolIntegrityViolation,
    compute_tool_hash,
)


def _make_mock_tool(
    name: str = "test_tool",
    description: str = "A test tool",
    parameters_schema: dict[str, JsonValue] | None = None,
) -> MagicMock:
    """Create a mock BaseTool with a to_definition() method."""
    from synthorg.providers.models import ToolDefinition

    tool = MagicMock()
    tool.name = name
    definition = ToolDefinition(
        name=name,
        description=description,
        parameters_schema=parameters_schema or {},
    )
    tool.to_definition.return_value = definition
    return tool


@pytest.mark.unit
class TestToolIntegrityCheckConfig:
    """Tests for ToolIntegrityCheckConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = ToolIntegrityCheckConfig()
        assert config.enabled is True
        assert config.hashes_file is None
        assert config.fail_on_violation is False

    def test_frozen(self) -> None:
        config = ToolIntegrityCheckConfig()
        with pytest.raises(ValidationError):
            config.enabled = False  # type: ignore[misc]

    def test_custom_values(self, tmp_path: Path) -> None:
        hashes_path = tmp_path / "hashes.json"
        config = ToolIntegrityCheckConfig(
            enabled=False,
            hashes_file=hashes_path,
            fail_on_violation=True,
        )
        assert config.enabled is False
        assert config.hashes_file == hashes_path
        assert config.fail_on_violation is True


@pytest.mark.unit
class TestComputeToolHash:
    """Tests for compute_tool_hash determinism."""

    def test_deterministic_same_tool(self) -> None:
        tool = _make_mock_tool()
        h1 = compute_tool_hash(tool.to_definition())
        h2 = compute_tool_hash(tool.to_definition())
        assert h1 == h2

    def test_hash_is_64_char_hex(self) -> None:
        tool = _make_mock_tool()
        h = compute_tool_hash(tool.to_definition())
        assert len(h) == 64
        int(h, 16)  # Should not raise.

    def test_hash_changes_on_description_change(self) -> None:
        tool1 = _make_mock_tool(description="Version 1")
        tool2 = _make_mock_tool(description="Version 2")
        h1 = compute_tool_hash(tool1.to_definition())
        h2 = compute_tool_hash(tool2.to_definition())
        assert h1 != h2

    def test_hash_changes_on_schema_change(self) -> None:
        tool1 = _make_mock_tool(
            parameters_schema={
                "type": "object",
                "properties": {"a": {"type": "string"}},
            },
        )
        tool2 = _make_mock_tool(
            parameters_schema={
                "type": "object",
                "properties": {"b": {"type": "integer"}},
            },
        )
        h1 = compute_tool_hash(tool1.to_definition())
        h2 = compute_tool_hash(tool2.to_definition())
        assert h1 != h2

    def test_hash_changes_on_name_change(self) -> None:
        tool1 = _make_mock_tool(name="tool_a")
        tool2 = _make_mock_tool(name="tool_b")
        h1 = compute_tool_hash(tool1.to_definition())
        h2 = compute_tool_hash(tool2.to_definition())
        assert h1 != h2


@pytest.mark.unit
class TestToolIntegrityViolation:
    """Tests for ToolIntegrityViolation frozen model."""

    def test_frozen(self) -> None:
        violation = ToolIntegrityViolation(
            tool_name="test_tool",
            expected_hash="a" * 64,
            actual_hash="b" * 64,
        )
        assert violation.tool_name == "test_tool"
        with pytest.raises(ValidationError):
            violation.tool_name = "x"  # type: ignore[misc]


@pytest.mark.unit
class TestToolIntegrityReport:
    """Tests for ToolIntegrityReport frozen model."""

    def test_no_violations(self) -> None:
        report = ToolIntegrityReport(
            violations=(),
            current_hashes={"tool_a": "a" * 64},
            checked_at=datetime.now(UTC),
        )
        assert len(report.violations) == 0

    def test_has_violations(self) -> None:
        v = ToolIntegrityViolation(
            tool_name="tool_a",
            expected_hash="a" * 64,
            actual_hash="b" * 64,
        )
        report = ToolIntegrityReport(
            violations=(v,),
            current_hashes={"tool_a": "b" * 64},
            checked_at=datetime.now(UTC),
        )
        assert len(report.violations) == 1


@pytest.mark.unit
class TestToolIntegrityChecker:
    """Tests for ToolRegistryIntegrityChecker."""

    def test_no_violations_without_prior_hashes(self) -> None:
        checker = ToolIntegrityChecker()
        tool = _make_mock_tool()
        report = checker.check((tool,))
        assert len(report.violations) == 0
        assert "test_tool" in report.current_hashes

    def test_no_violations_when_hashes_match(self) -> None:
        tool = _make_mock_tool()
        expected_hash = compute_tool_hash(tool.to_definition())
        checker = ToolIntegrityChecker(
            prior_hashes={"test_tool": expected_hash},
        )
        report = checker.check((tool,))
        assert len(report.violations) == 0

    def test_violation_on_hash_mismatch(self) -> None:
        tool = _make_mock_tool()
        checker = ToolIntegrityChecker(
            prior_hashes={"test_tool": "x" * 64},
        )
        report = checker.check((tool,))
        assert len(report.violations) == 1
        assert report.violations[0].tool_name == "test_tool"
        assert report.violations[0].expected_hash == "x" * 64

    def test_new_tool_not_in_prior_is_not_violation(self) -> None:
        """A new tool not in prior_hashes should not be a violation."""
        tool = _make_mock_tool(name="new_tool")
        checker = ToolIntegrityChecker(
            prior_hashes={"old_tool": "a" * 64},
        )
        report = checker.check((tool,))
        assert len(report.violations) == 0
        assert "new_tool" in report.current_hashes

    def test_multiple_tools(self) -> None:
        tool_a = _make_mock_tool(name="tool_a", description="A")
        tool_b = _make_mock_tool(name="tool_b", description="B")
        hash_a = compute_tool_hash(tool_a.to_definition())

        checker = ToolIntegrityChecker(
            prior_hashes={
                "tool_a": hash_a,
                "tool_b": "a" * 64,  # valid-length hex hash
            },
        )
        report = checker.check((tool_a, tool_b))
        # tool_a matches, tool_b does not.
        assert len(report.violations) == 1
        assert report.violations[0].tool_name == "tool_b"

    def test_report_contains_all_current_hashes(self) -> None:
        tool_a = _make_mock_tool(name="tool_a")
        tool_b = _make_mock_tool(name="tool_b")
        checker = ToolIntegrityChecker()
        report = checker.check((tool_a, tool_b))
        assert "tool_a" in report.current_hashes
        assert "tool_b" in report.current_hashes

    def test_checked_at_is_utc(self) -> None:
        checker = ToolIntegrityChecker()
        tool = _make_mock_tool()
        report = checker.check((tool,))
        assert report.checked_at.tzinfo is not None


@pytest.mark.unit
class TestToolIntegrityCheckProperties:
    """Property-based tests for tool integrity checking."""

    @given(
        name=st.text(
            alphabet=st.characters(categories=("L", "N")),
            min_size=1,
            max_size=50,
        ),
        desc=st.text(
            alphabet=st.characters(categories=("L", "N", "Z")),
            min_size=0,
            max_size=200,
        ),
    )
    @settings(max_examples=50)
    def test_hash_is_always_64_hex(self, name: str, desc: str) -> None:
        tool = _make_mock_tool(name=name, description=desc)
        h = compute_tool_hash(tool.to_definition())
        assert len(h) == 64
        int(h, 16)

    @given(
        name=st.text(
            alphabet=st.characters(categories=("L", "N")),
            min_size=1,
            max_size=50,
        ),
    )
    @settings(max_examples=50)
    def test_hash_deterministic(self, name: str) -> None:
        tool = _make_mock_tool(name=name)
        h1 = compute_tool_hash(tool.to_definition())
        h2 = compute_tool_hash(tool.to_definition())
        assert h1 == h2
