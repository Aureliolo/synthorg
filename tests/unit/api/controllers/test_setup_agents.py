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
from synthorg.core.enums import CompanyType, SeniorityLevel
from synthorg.templates.schema import (
    CompanyTemplate,
    TemplateAgentConfig,
    TemplateMetadata,
)


def _make_template(agents: list[dict[str, Any]]) -> CompanyTemplate:
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
    def test_dict_model_produces_model_requirement(self) -> None:
        """Dict model in template populates model_requirement in output."""
        template = _make_template(
            [
                {
                    "role": "CEO",
                    "model": {
                        "tier": "large",
                        "priority": "quality",
                        "min_context": 100_000,
                    },
                },
            ]
        )
        agents = expand_template_agents(template)
        assert len(agents) == 1
        agent = agents[0]
        assert agent["tier"] == "large"
        assert "model_requirement" in agent
        req = agent["model_requirement"]
        assert req["tier"] == "large"
        assert req["priority"] == "quality"
        assert req["min_context"] == 100_000

    def test_string_model_has_no_model_requirement(self) -> None:
        """String model in template does not produce model_requirement."""
        template = _make_template(
            [
                {"role": "Developer", "model": "medium"},
            ]
        )
        agents = expand_template_agents(template)
        assert len(agents) == 1
        agent = agents[0]
        assert agent["tier"] == "medium"
        assert "model_requirement" not in agent

    def test_mixed_models_in_same_template(self) -> None:
        """Dict and string models coexist in the same template."""
        template = _make_template(
            [
                {
                    "role": "CEO",
                    "model": {"tier": "large", "priority": "quality"},
                },
                {"role": "Developer", "model": "small"},
            ]
        )
        agents = expand_template_agents(template)
        assert len(agents) == 2

        ceo = next(a for a in agents if a["role"] == "CEO")
        dev = next(a for a in agents if a["role"] == "Developer")

        assert "model_requirement" in ceo
        assert ceo["tier"] == "large"
        assert "model_requirement" not in dev
        assert dev["tier"] == "small"

    def test_dict_model_empty_uses_defaults(self) -> None:
        """Empty dict model produces defaults in model_requirement."""
        template = _make_template(
            [
                {"role": "Dev", "model": {}},
            ]
        )
        agents = expand_template_agents(template)
        assert len(agents) == 1
        agent = agents[0]
        assert agent["tier"] == "medium"
        assert "model_requirement" in agent
        assert agent["model_requirement"]["tier"] == "medium"
        assert agent["model_requirement"]["priority"] == "balanced"


@pytest.mark.unit
class TestExpandTemplateAgentsCustomPresets:
    def test_custom_preset_resolved(self) -> None:
        """Custom preset is used when passed to expand_template_agents."""
        custom = {
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
        agents = expand_template_agents(template, custom_presets=custom)
        assert len(agents) == 1
        assert agents[0]["personality"]["communication_style"] == "custom"
        assert agents[0]["personality_preset"] == "my_custom"

    def test_unknown_preset_falls_back_to_pragmatic_builder(self) -> None:
        """Unknown preset falls back to pragmatic_builder in setup path."""
        template = _make_template(
            [{"role": "Dev", "personality_preset": "nonexistent"}]
        )
        agents = expand_template_agents(template)
        assert len(agents) == 1
        assert agents[0]["personality_preset"] == "pragmatic_builder"

    def test_builtin_preset_works_with_custom_presets(self) -> None:
        """Builtin presets still work when custom_presets dict is passed."""
        custom = {"other": {"traits": ("a",)}}
        template = _make_template(
            [{"role": "Dev", "personality_preset": "pragmatic_builder"}]
        )
        agents = expand_template_agents(template, custom_presets=custom)
        assert len(agents) == 1
        assert agents[0]["personality"]["communication_style"] == "concise"


@pytest.mark.unit
class TestBuildAgentConfigCustomPresets:
    def _make_request(
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
        result = build_agent_config(data)
        assert result["personality"]["communication_style"] == "concise"
        assert result["personality_preset"] == "pragmatic_builder"

    def test_custom_preset_resolves(self) -> None:
        custom = {
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
        result = build_agent_config(data, custom_presets=custom)
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

        agents: list[dict[str, Any]] = [
            {"name": "Agent-0", "tier": tier},
        ]
        result = match_and_assign_models(agents, {})

        assert len(result) == 1
        model = result[0]["model"]
        assert model["provider"] == "test-provider"
        assert model["model_id"] == model_id
        assert model["model_tier"] == tier
