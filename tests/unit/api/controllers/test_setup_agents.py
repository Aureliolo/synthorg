"""Tests for expand_template_agents, match_and_assign_models, and build_agent_config."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from synthorg.api.controllers.setup_agents import (
    build_agent_config,
    expand_template_agents,
    match_and_assign_models,
)
from synthorg.core.domain_errors import ValidationError
from synthorg.hr.seniority import SeniorityLevel
from synthorg.organization.enums import CompanyType
from synthorg.templates.schema import (
    CompanyTemplate,
    TemplateAgentConfig,
    TemplateMetadata,
)
from tests._shared import JsonDict


def _make_template(agents: list[JsonDict]) -> CompanyTemplate:
    """Build a minimal CompanyTemplate with the given agent configs."""
    agent_cfgs = tuple(TemplateAgentConfig(**a) for a in agents)
    return CompanyTemplate(
        metadata=TemplateMetadata(
            name="test-template",
            company_type=CompanyType.CUSTOM,
        ),
        agents=agent_cfgs,
    )


@pytest.mark.unit
class TestExpandTemplateAgentsDictModel:
    def test_capability_dict_produces_model_requirement(self) -> None:
        """A capability dict populates model_requirement (no tier collapse)."""
        template = _make_template(
            [
                {
                    "role": "CEO",
                    "model": {
                        "priority": "quality",
                        "min_context": 100_000,
                        "requires_reasoning": True,
                    },
                },
            ]
        )
        agents: list[JsonDict] = expand_template_agents(template)
        assert len(agents) == 1
        agent = agents[0]
        # Pre-match the agent carries no resolved tier (set by the matcher).
        assert "tier" not in agent
        assert "model_requirement" in agent
        req = agent["model_requirement"]
        assert "tier" not in req
        assert req["priority"] == "quality"
        assert req["min_context"] == 100_000
        assert req["requires_reasoning"] is True

    def test_string_model_pins_explicit_id(self) -> None:
        """A string model is an explicit model_id pin in model_requirement."""
        template = _make_template(
            [
                {"role": "Developer", "model": "example-medium-001"},
            ]
        )
        agents: list[JsonDict] = expand_template_agents(template)
        assert len(agents) == 1
        agent = agents[0]
        assert "model_requirement" in agent
        assert agent["model_requirement"]["model_id"] == "example-medium-001"

    def test_mixed_models_in_same_template(self) -> None:
        """Capability-dict and explicit-id models coexist in one template."""
        template = _make_template(
            [
                {
                    "role": "CEO",
                    "model": {"priority": "quality", "requires_reasoning": True},
                },
                {"role": "Developer", "model": "example-small-001"},
            ]
        )
        agents: list[JsonDict] = expand_template_agents(template)
        assert len(agents) == 2

        ceo = next(a for a in agents if a["role"] == "CEO")
        dev = next(a for a in agents if a["role"] == "Developer")

        assert ceo["model_requirement"]["priority"] == "quality"
        assert ceo["model_requirement"]["model_id"] is None
        assert dev["model_requirement"]["model_id"] == "example-small-001"

    def test_dict_model_empty_uses_defaults(self) -> None:
        """An empty dict model resolves to the balanced default requirement."""
        template = _make_template(
            [
                {"role": "Dev", "model": {}},
            ]
        )
        agents: list[JsonDict] = expand_template_agents(template)
        assert len(agents) == 1
        agent = agents[0]
        assert "model_requirement" in agent
        assert agent["model_requirement"]["priority"] == "balanced"
        assert agent["model_requirement"]["model_id"] is None


@pytest.mark.unit
class TestExpandTemplateAgentsCustomPresets:
    def test_custom_preset_resolved(self) -> None:
        """Custom preset is used when passed to expand_template_agents."""
        custom: dict[str, JsonDict] = {
            "my_custom": {
                "traits": ("custom-trait",),
                "communication_style": "custom",
                "description": "Custom",
                "openness": 0.5,
                "conscientiousness": 0.5,
                "extraversion": 0.5,
                "agreeableness": 0.5,
                "stress_response": 0.5,
            },
        }
        template = _make_template([{"role": "Dev", "personality_preset": "my_custom"}])
        agents: list[JsonDict] = expand_template_agents(template, custom_presets=custom)
        assert len(agents) == 1
        assert agents[0]["personality"]["communication_style"] == "custom"
        assert agents[0]["personality_preset"] == "my_custom"

    def test_unknown_preset_falls_back_to_pragmatic_builder(self) -> None:
        """Unknown preset falls back to pragmatic_builder in setup path."""
        template = _make_template(
            [{"role": "Dev", "personality_preset": "nonexistent"}]
        )
        agents: list[JsonDict] = expand_template_agents(template)
        assert len(agents) == 1
        assert agents[0]["personality_preset"] == "pragmatic_builder"

    def test_builtin_preset_works_with_custom_presets(self) -> None:
        """Builtin presets still work when custom_presets dict is passed."""
        custom: dict[str, JsonDict] = {"other": {"traits": ("a",)}}
        template = _make_template(
            [{"role": "Dev", "personality_preset": "pragmatic_builder"}]
        )
        agents: list[JsonDict] = expand_template_agents(template, custom_presets=custom)
        assert len(agents) == 1
        assert agents[0]["personality"]["communication_style"] == "concise"


@pytest.mark.unit
class TestBuildAgentConfigCustomPresets:
    def _make_request(  # type: ignore[explicit-any]  # returns a MagicMock request stub
        self,
        preset: str = "pragmatic_builder",
    ) -> Any:
        req = MagicMock()
        req.name = "Test Agent"
        req.role = "Backend Developer"
        req.department = "engineering"
        req.level = SeniorityLevel.MID
        req.personality_preset = preset
        req.model_provider = "test-provider"
        req.model_id = "test-small-001"
        req.budget_limit_monthly = None
        return req

    def test_builtin_preset_resolves(self) -> None:
        data = self._make_request("pragmatic_builder")
        result: JsonDict = build_agent_config(data)
        assert result["personality"]["communication_style"] == "concise"
        assert result["personality_preset"] == "pragmatic_builder"

    def test_custom_preset_resolves(self) -> None:
        custom: dict[str, JsonDict] = {
            "my_custom": {
                "traits": ("custom-trait",),
                "communication_style": "custom",
                "description": "Custom",
                "openness": 0.5,
                "conscientiousness": 0.5,
                "extraversion": 0.5,
                "agreeableness": 0.5,
                "stress_response": 0.5,
            },
        }
        data = self._make_request("my_custom")
        result: JsonDict = build_agent_config(data, custom_presets=custom)
        assert result["personality"]["communication_style"] == "custom"

    def test_unknown_preset_raises_validation_error(self) -> None:
        data = self._make_request("nonexistent")
        with pytest.raises(ValidationError, match="Unknown personality preset"):
            build_agent_config(data)


@pytest.mark.unit
class TestMatchAndAssignModels:
    """Tests for match_and_assign_models model_tier wiring."""

    @pytest.mark.parametrize(
        ("tier", "model_id"),
        [
            ("large", "test-large-001"),
            ("small", "test-small-001"),
        ],
    )
    @patch("synthorg.templates.model_matcher.match_all_agents")
    def test_model_tier_propagated(
        self,
        mock_match: MagicMock,
        tier: str,
        model_id: str,
    ) -> None:
        """model_tier from the match is included in the agent model dict."""
        match = MagicMock()
        match.agent_index = 0
        match.provider_name = "test-provider"
        match.model_id = model_id
        match.tier = tier
        mock_match.return_value = [match]

        agents: list[JsonDict] = [
            {"name": "Agent-0", "tier": tier},
        ]
        result: list[JsonDict] = match_and_assign_models(agents, {})

        assert len(result) == 1
        model = result[0]["model"]
        assert model["provider"] == "test-provider"
        assert model["model_id"] == model_id
        assert model["model_tier"] == tier
