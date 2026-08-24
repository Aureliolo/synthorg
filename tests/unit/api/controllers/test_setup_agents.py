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
    ProviderModelCoverageInsufficientError,
)
from synthorg.core.types import CapabilityLevel
from synthorg.templates.loader import load_template
from synthorg.templates.model_matcher import ModelMatch
from tests._shared import JsonDict


@pytest.mark.unit
class TestExpandTemplateAgentsRenders:
    """expand_template_agents renders through the one renderer pipeline.

    The model-block resolution itself is covered at the renderer layer
    (``test_renderer.py``); these assert the wizard wrapper renders the
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


@pytest.mark.unit
class TestBuildAgentConfig:
    """build_agent_config projects the wizard payload onto a settings row."""

    def _request(self, *, budget: float | None = None) -> SetupAgentRequest:
        return SetupAgentRequest(
            name="Test Agent",
            role="Backend Developer",
            department="engineering",
            model_provider="test-provider",
            model_id="test-basic-001",
            budget_limit_monthly=budget,
        )

    def test_carries_identity_and_bound_pair(self) -> None:
        """The row names the agent and both halves of its binding."""
        result = build_agent_config(self._request())

        assert result["name"] == "Test Agent"
        assert result["role"] == "Backend Developer"
        assert result["department"] == "engineering"
        assert result["model"] == {
            "provider": "test-provider",
            "model_id": "test-basic-001",
        }

    def test_omits_budget_when_unset(self) -> None:
        """An absent budget leaves the key out rather than writing a null."""
        assert "budget_limit_monthly" not in build_agent_config(self._request())

    def test_carries_budget_when_set(self) -> None:
        """A supplied budget reaches the persisted row."""
        result = build_agent_config(self._request(budget=250.0))

        assert result["budget_limit_monthly"] == 250.0


@pytest.mark.unit
class TestMatchAndAssignModels:
    """Tests for match_and_assign_models capability wiring."""

    @pytest.mark.parametrize(
        ("capability", "model_id"),
        [
            ("expert", "test-expert-001"),
            ("basic", "test-basic-001"),
        ],
    )
    @patch("synthorg.templates.model_matcher.match_all_agents")
    def test_capability_propagated(
        self,
        mock_match: MagicMock,
        capability: str,
        model_id: str,
    ) -> None:
        """The match's capability is included in the agent model dict."""
        match = ModelMatch(
            agent_index=0,
            provider_name="test-provider",
            model_id=model_id,
            capability=cast("CapabilityLevel", capability),
            score=1.0,
        )
        mock_match.return_value = [match]

        agents: list[JsonDict] = [{"name": "Agent-0"}]
        result: list[JsonDict] = match_and_assign_models(agents, {})

        assert len(result) == 1
        model = result[0]["model"]
        assert model["provider"] == "test-provider"
        assert model["model_id"] == model_id
        assert model["capability"] == capability
        # And nowhere else: a sibling copy at the agent's top level is a rung
        # nothing revises, which is what the dashboard used to print.
        assert "capability" not in result[0]

    @patch("synthorg.templates.model_matcher.match_all_agents")
    def test_partial_assignment_is_allowed(self, mock_match: MagicMock) -> None:
        """One unmatched agent is reported but does not block setup."""
        mock_match.return_value = [
            ModelMatch(
                agent_index=0,
                provider_name="test-provider",
                model_id="test-expert-001",
                capability="expert",
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

        with pytest.raises(ProviderModelCoverageInsufficientError):
            match_and_assign_models(agents, {})

    @patch("synthorg.templates.model_matcher.match_all_agents")
    def test_empty_roster_is_not_starvation(self, mock_match: MagicMock) -> None:
        """No agents to assign is not the same as no model for any agent."""
        mock_match.return_value = []

        assert match_and_assign_models([], {}) == []
