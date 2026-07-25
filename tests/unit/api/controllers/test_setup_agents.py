"""Tests for expand_template_agents, match_and_assign_models, and build_agent_config."""

from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from synthorg.api.controllers.setup_agents import (
    build_agent_config,
    expand_template_agents,
)
from synthorg.api.controllers.setup_model_assignment import match_and_assign_models
from synthorg.api.controllers.setup_models import SetupAgentRequest
from synthorg.core.domain_errors import (
    ProviderTierCoverageInsufficientError,
    ValidationError,
)
from synthorg.core.types import ModelTier
from synthorg.templates.loader import load_template
from synthorg.templates.model_matcher import ModelMatch
from tests._shared import JsonDict


@pytest.mark.unit
class TestExpandTemplateAgentsRenders:
    """expand_template_agents renders through the one renderer pipeline.

    The model-block / preset resolution itself is covered at the renderer
    layer (``test_renderer.py``); these assert the wizard wrapper renders the
    real, inheritance-resolved roster and projects each agent's
    ``model_requirement`` for matching.
    """

    def test_renders_roster_with_requirements(self) -> None:
        """Every rendered agent carries a model_requirement for the matcher."""
        agents: list[JsonDict] = expand_template_agents(
            load_template("startup"), locales=["en_US"]
        )
        assert agents
        assert all(a.get("model_requirement") is not None for a in agents)

    def test_head_role_ceo_is_materialised_strategic(self) -> None:
        """A department head-role CEO is materialised and strategic.

        Proves inheritance + head-role resolution (absent in the old
        load-only path) and the strategic-role default: a spec-less CEO
        resolves to quality + reasoning rather than a mid-tier balanced.
        """
        agents: list[JsonDict] = expand_template_agents(
            load_template("startup"), locales=["en_US"]
        )
        ceo = next(a for a in agents if a["role"] == "CEO")
        req = ceo["model_requirement"]
        assert req["priority"] == "quality"
        assert req["requires_reasoning"] is True

    def test_inheritance_and_added_execs_resolve(self) -> None:
        """product_team renders its CEO+CTO with top-tier requirements."""
        agents: list[JsonDict] = expand_template_agents(
            load_template("product_team"), locales=["en_US"]
        )
        roles = [a["role"] for a in agents]
        assert "CEO" in roles
        assert "CTO" in roles
        cto = next(a for a in agents if a["role"] == "CTO")
        assert cto["model_requirement"]["priority"] == "quality"
        assert cto["model_requirement"]["requires_reasoning"] is True

    def test_custom_presets_accepted(self) -> None:
        """A custom_presets map passes through render without error."""
        custom: dict[str, JsonDict] = {"other": {"traits": ("a",)}}
        agents: list[JsonDict] = expand_template_agents(
            load_template("startup"), locales=["en_US"], custom_presets=custom
        )
        assert agents


@pytest.mark.unit
class TestBuildAgentConfigCustomPresets:
    def _make_request(
        self,
        preset: str = "pragmatic_builder",
    ) -> SetupAgentRequest:
        # ``model_construct`` builds a real, typed ``SetupAgentRequest`` while
        # bypassing validation: the model validates ``personality_preset``
        # against the built-in catalogue, but these tests exercise
        # ``build_agent_config`` with custom / unknown presets that the model
        # would reject at construction.
        return SetupAgentRequest.model_construct(
            name="Test Agent",
            role="Backend Developer",
            department="engineering",
            personality_preset=preset,
            model_provider="test-provider",
            model_id="test-small-001",
            budget_limit_monthly=None,
        )

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
        match = ModelMatch(
            agent_index=0,
            provider_name="test-provider",
            model_id=model_id,
            tier=cast("ModelTier", tier),
            score=1.0,
        )
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

    @patch("synthorg.templates.model_matcher.match_all_agents")
    def test_partial_assignment_is_allowed(self, mock_match: MagicMock) -> None:
        """One unmatched agent is reported but does not block setup."""
        mock_match.return_value = [
            ModelMatch(
                agent_index=0,
                provider_name="test-provider",
                model_id="test-large-001",
                tier=cast("ModelTier", "large"),
                score=1.0,
            )
        ]
        agents: list[JsonDict] = [{"name": "Agent-0"}, {"name": "Agent-1"}]

        result: list[JsonDict] = match_and_assign_models(agents, {})

        assert len(result) == 2
        assert result[1].get("model") is None

    @patch("synthorg.templates.model_matcher.match_all_agents")
    def test_wholly_unassigned_roster_is_refused(self, mock_match: MagicMock) -> None:
        """A roster where nothing matched cannot do work, so setup fails loud.

        The pre-flight provider gate only rejects an empty catalogue, so a
        catalogue whose models all fail the capability floors reaches here.
        """
        mock_match.return_value = []
        agents: list[JsonDict] = [{"name": "Agent-0"}, {"name": "Agent-1"}]

        with pytest.raises(ProviderTierCoverageInsufficientError):
            match_and_assign_models(agents, {})

    @patch("synthorg.templates.model_matcher.match_all_agents")
    def test_empty_roster_is_not_starvation(self, mock_match: MagicMock) -> None:
        """No agents to assign is not the same as no model for any agent."""
        mock_match.return_value = []

        assert match_and_assign_models([], {}) == []
