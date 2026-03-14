"""Tests for prompt template constants and autonomy instructions."""

import pytest

from synthorg.core.enums import SeniorityLevel
from synthorg.engine.prompt_template import (
    AUTONOMY_INSTRUCTIONS,
    DEFAULT_TEMPLATE,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestAutonomyInstructions:
    """Tests for AUTONOMY_INSTRUCTIONS coverage and content."""

    def test_all_seniority_levels_covered(self) -> None:
        assert set(SeniorityLevel) == set(AUTONOMY_INSTRUCTIONS)

    def test_all_values_are_non_empty_strings(self) -> None:
        for level, instruction in AUTONOMY_INSTRUCTIONS.items():
            assert isinstance(instruction, str), f"{level} value is not a string"
            assert instruction.strip(), f"{level} has empty instruction text"

    def test_each_level_produces_different_text(self) -> None:
        values = list(AUTONOMY_INSTRUCTIONS.values())
        assert len(values) == len(set(values))


@pytest.mark.unit
class TestDefaultTemplate:
    """Tests for the default Jinja2 system prompt template."""

    def test_default_template_is_non_empty(self) -> None:
        assert isinstance(DEFAULT_TEMPLATE, str)
        assert DEFAULT_TEMPLATE.strip()


@pytest.mark.unit
class TestAutonomyInstructionsGuard:
    """Tests for the module-level guard that detects missing levels."""

    def test_guard_detects_missing_level(self) -> None:
        incomplete = dict(AUTONOMY_INSTRUCTIONS)
        removed_key = next(iter(incomplete))
        del incomplete[removed_key]

        missing = set(SeniorityLevel) - set(incomplete)
        assert removed_key in missing
