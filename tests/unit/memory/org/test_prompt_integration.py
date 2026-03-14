"""Tests for org_policies integration in system prompt."""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import SeniorityLevel
from synthorg.engine.prompt import build_system_prompt
from synthorg.engine.prompt_template import PROMPT_TEMPLATE_VERSION

pytestmark = pytest.mark.timeout(30)


def _make_agent() -> AgentIdentity:
    """Build a minimal AgentIdentity for prompt tests."""
    return AgentIdentity(
        name="Test Agent",
        role="Developer",
        department="Engineering",
        level=SeniorityLevel.MID,
        model=ModelConfig(
            provider="test-provider",
            model_id="test-model-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


@pytest.mark.unit
class TestOrgPoliciesPromptIntegration:
    """org_policies section is rendered in system prompts."""

    def test_no_policies_no_section(self) -> None:
        agent = _make_agent()
        result = build_system_prompt(agent=agent)
        assert "Organizational Policies" not in result.content
        assert "org_policies" not in result.sections

    def test_policies_rendered_in_prompt(self) -> None:
        agent = _make_agent()
        result = build_system_prompt(
            agent=agent,
            org_policies=("No secrets in code", "All PRs need review"),
        )
        assert "Organizational Policies" in result.content
        assert "No secrets in code" in result.content
        assert "All PRs need review" in result.content
        assert "org_policies" in result.sections

    def test_template_version_updated(self) -> None:
        assert PROMPT_TEMPLATE_VERSION == "1.4.0"

    def test_policies_trimmed_under_budget(self) -> None:
        agent = _make_agent()
        result = build_system_prompt(
            agent=agent,
            org_policies=("Very important policy",),
            max_tokens=10,
        )
        assert "org_policies" not in result.sections
