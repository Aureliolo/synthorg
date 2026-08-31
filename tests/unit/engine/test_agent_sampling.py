"""An agent's binding is the single owner of how its model is sampled."""

import inspect
from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_sampling import binding_sampling
from synthorg.providers.models import CompletionConfig
from tests._shared import as_uuid


def _identity_with(**overrides: object) -> AgentIdentity:
    """Build a roster identity whose binding carries *overrides*.

    Returns:
        The identity.
    """
    return AgentIdentity(
        id=as_uuid("sampling-agent"),
        name=NotBlankStr("Sampler"),
        role=NotBlankStr("Developer"),
        department=NotBlankStr("Engineering"),
        hiring_date=date(2026, 1, 1),
        model=ModelConfig.model_validate(
            {"provider": "test-provider", "model_id": "test-model-001", **overrides}
        ),
    )


@pytest.mark.unit
class TestBindingSampling:
    """What one agent's binding declares reaches the request unchanged."""

    def test_carries_every_declared_dial(self) -> None:
        """Each field the binding states appears on the config built from it."""
        config = binding_sampling(
            _identity_with(
                temperature=1.0,
                top_p=0.95,
                reasoning_effort=ReasoningEffort.HIGH,
                max_tokens=131_072,
            )
        )
        assert config.temperature == pytest.approx(1.0)
        assert config.top_p == pytest.approx(0.95)
        assert config.reasoning_effort is ReasoningEffort.HIGH
        assert config.max_tokens == 131_072

    def test_unset_top_p_leaves_the_completion_default_standing(self) -> None:
        """An unstated threshold is not a threshold of this module's choosing.

        Copying ``CompletionConfig``'s default here would silently diverge from
        it the day that default moved, so the field is simply not set.
        """
        config = binding_sampling(_identity_with())
        assert config.top_p == CompletionConfig().top_p

    def test_unset_reasoning_effort_stays_unset(self) -> None:
        """Nothing is invented for an agent that asked for no depth.

        ``None`` is what lets the stakes ladder answer instead, so a value
        here would quietly take that decision away from it.
        """
        config = binding_sampling(_identity_with())
        assert config.reasoning_effort is None

    def test_unset_max_tokens_stays_unset(self) -> None:
        """An unstated ceiling still defers to the settings ladder."""
        config = binding_sampling(_identity_with())
        assert config.max_tokens is None


@pytest.mark.unit
class TestPlanningSessionSamplesFromTheBinding:
    """The planning loop reads the owner's binding, not a strategy default.

    A strategy-level temperature was a second answer to how the bound model
    should be sampled, and it could not be a right one: the value a vendor
    publishes is a property of the model, which a strategy config does not
    know. It shipped at 0.2 against work sessions at 0.7, and the quieter
    authority won wherever it happened to be read.
    """

    def test_agent_session_builds_its_config_from_the_owner(self) -> None:
        """The planning dispatch sources sampling from the owner identity."""
        from synthorg.engine.decomposition import agent_session

        source = inspect.getsource(agent_session)
        assert "binding_sampling(owner)" in source, (
            "the planning session must build its completion config from the "
            "owner's own binding, so planning and work sessions cannot sample "
            "the same model differently"
        )

    def test_strategy_config_declares_no_sampling(self) -> None:
        """The deleted second owner has not come back."""
        from synthorg.engine.decomposition.strategy_deps import (
            AgentSessionDecompositionConfig,
        )

        assert "temperature" not in AgentSessionDecompositionConfig.model_fields
        assert "top_p" not in AgentSessionDecompositionConfig.model_fields
