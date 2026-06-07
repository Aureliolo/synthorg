"""Tests for prompt template constants and autonomy instructions."""

import pytest

from synthorg.engine.prompt_template import (
    AUTONOMY_INSTRUCTIONS,
    AUTONOMY_MINIMAL,
    AUTONOMY_SUMMARY,
    DEFAULT_TEMPLATE,
)
from synthorg.hr.seniority import SeniorityLevel


@pytest.mark.unit
class TestDefaultTemplate:
    """Tests for the default Jinja2 system prompt template."""

    def test_default_template_is_non_empty(self) -> None:
        assert isinstance(DEFAULT_TEMPLATE, str)
        assert DEFAULT_TEMPLATE.strip()


_AUTONOMY_MAPS = {
    "full": AUTONOMY_INSTRUCTIONS,
    "summary": AUTONOMY_SUMMARY,
    "minimal": AUTONOMY_MINIMAL,
}


@pytest.mark.unit
class TestAutonomyMaps:
    """Tests for all three autonomy instruction maps (full/summary/minimal)."""

    @pytest.mark.parametrize("label", ["full", "summary", "minimal"])
    def test_all_seniority_levels_covered(self, label: str) -> None:
        assert set(SeniorityLevel) == set(_AUTONOMY_MAPS[label])

    @pytest.mark.parametrize("label", ["full", "summary", "minimal"])
    def test_all_values_are_non_empty_strings(self, label: str) -> None:
        for level, instruction in _AUTONOMY_MAPS[label].items():
            assert isinstance(instruction, str), f"{level} value is not a string"
            assert instruction.strip(), f"{level} has empty instruction text"

    @pytest.mark.parametrize("label", ["full", "summary", "minimal"])
    def test_each_level_produces_different_text(self, label: str) -> None:
        values = list(_AUTONOMY_MAPS[label].values())
        assert len(values) == len(set(values))

    def test_summary_shorter_than_full(self) -> None:
        """Summary instructions are shorter than full instructions."""
        for level in SeniorityLevel:
            assert len(AUTONOMY_SUMMARY[level]) <= len(
                AUTONOMY_INSTRUCTIONS[level],
            )

    def test_minimal_shorter_than_summary(self) -> None:
        """Minimal instructions are shorter than summary instructions."""
        for level in SeniorityLevel:
            assert len(AUTONOMY_MINIMAL[level]) <= len(
                AUTONOMY_SUMMARY[level],
            )


@pytest.mark.unit
class TestAutonomyInstructionsGuard:
    """Tests for the detection logic pattern used by module-level guards.

    The actual import-time guards in ``prompt_template.py`` and
    ``prompt_profiles.py`` run at module load and cannot be re-exercised
    after import.  This test verifies that the *detection logic* (set
    subtraction) correctly identifies missing levels, giving confidence
    that the guards work.
    """

    def test_guard_detects_missing_level(self) -> None:
        incomplete = dict(AUTONOMY_INSTRUCTIONS)
        removed_key = next(iter(incomplete))
        del incomplete[removed_key]

        missing = set(SeniorityLevel) - set(incomplete)
        assert removed_key in missing
