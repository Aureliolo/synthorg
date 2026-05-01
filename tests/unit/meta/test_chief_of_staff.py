"""Unit tests for Chief of Staff role and prompts."""

import pytest

from synthorg.engine.prompt_safety import (
    TAG_CONFIG_VALUE,
    TAG_TASK_DATA,
    untrusted_content_directive,
)
from synthorg.meta.chief_of_staff.prompts import (
    PROPOSAL_GENERATION_PROMPT,
    REGRESSION_EXPLANATION_PROMPT,
    SIGNAL_ANALYSIS_PROMPT,
    SIGNAL_CORRELATION_PROMPT,
)
from synthorg.meta.chief_of_staff.role import (
    REQUIRED_SKILLS,
    ROLE_DEPARTMENT,
    ROLE_NAME,
    TOOL_ACCESS,
    get_role_definition,
)

pytestmark = pytest.mark.unit


class TestChiefOfStaffRole:
    """Chief of Staff role definition tests."""

    def test_role_name(self) -> None:
        assert ROLE_NAME == "Chief of Staff"

    def test_role_department(self) -> None:
        assert ROLE_DEPARTMENT == "Executive"

    def test_required_skills(self) -> None:
        assert len(REQUIRED_SKILLS) >= 3
        assert "organizational_analysis" in REQUIRED_SKILLS

    def test_tool_access(self) -> None:
        assert len(TOOL_ACCESS) == 9
        assert all(t.startswith("synthorg_signals_") for t in TOOL_ACCESS)

    def test_role_definition_structure(self) -> None:
        defn = get_role_definition()
        assert defn["name"] == ROLE_NAME
        assert defn["department"] == ROLE_DEPARTMENT
        assert defn["authority_level"] == "vp"
        assert defn["tool_access"] == TOOL_ACCESS


class TestChiefOfStaffPrompts:
    """Chief of Staff prompt template tests."""

    def test_signal_analysis_has_placeholders(self) -> None:
        assert "{company_name}" in SIGNAL_ANALYSIS_PROMPT
        assert "{signal_summary}" in SIGNAL_ANALYSIS_PROMPT

    def test_proposal_generation_has_placeholders(self) -> None:
        assert "{rule_name}" in PROPOSAL_GENERATION_PROMPT
        assert "{config_section}" in PROPOSAL_GENERATION_PROMPT

    def test_regression_has_placeholders(self) -> None:
        assert "{metric_name}" in REGRESSION_EXPLANATION_PROMPT
        assert "{proposal_title}" in REGRESSION_EXPLANATION_PROMPT

    def test_prompts_are_non_empty(self) -> None:
        assert len(SIGNAL_ANALYSIS_PROMPT) > 100
        assert len(PROPOSAL_GENERATION_PROMPT) > 100
        assert len(REGRESSION_EXPLANATION_PROMPT) > 100


class TestSec1ChiefOfStaffTemplatesCarryDirective:
    """Every Chief of Staff template that interpolates
    attacker-controllable content (`company_name`, `signal_summary`,
    `snapshot_summary`, rule metadata, proposal fields, regression
    fields) must append ``untrusted_content_directive`` so the model
    is explicitly told the fenced fields are untrusted data.
    """

    def test_signal_analysis_carries_directive(self) -> None:
        directive = untrusted_content_directive(
            (TAG_CONFIG_VALUE, TAG_TASK_DATA),
        )
        assert directive in SIGNAL_ANALYSIS_PROMPT

    def test_signal_correlation_carries_directive(self) -> None:
        directive = untrusted_content_directive((TAG_TASK_DATA,))
        assert directive in SIGNAL_CORRELATION_PROMPT

    def test_proposal_generation_carries_directive(self) -> None:
        directive = untrusted_content_directive(
            (TAG_CONFIG_VALUE, TAG_TASK_DATA),
        )
        assert directive in PROPOSAL_GENERATION_PROMPT

    def test_regression_explanation_carries_directive(self) -> None:
        directive = untrusted_content_directive(
            (TAG_CONFIG_VALUE, TAG_TASK_DATA),
        )
        assert directive in REGRESSION_EXPLANATION_PROMPT
