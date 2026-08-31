"""An agent's binding is the single owner of how its model is sampled."""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_sampling import resolve_sampling
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
class TestTheBindingAnswersWhenNobodyElseDid:
    """What one agent's binding declares reaches the request unchanged."""

    def test_carries_every_declared_dial(self) -> None:
        """Each field the binding states appears on the config built from it."""
        config = resolve_sampling(
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
        config = resolve_sampling(_identity_with())
        assert config.top_p == pytest.approx(CompletionConfig().top_p)

    def test_unset_reasoning_effort_stays_unset(self) -> None:
        """Nothing is invented for an agent that asked for no depth.

        ``None`` is what lets the stakes ladder answer instead, so a value
        here would quietly take that decision away from it.
        """
        config = resolve_sampling(_identity_with())
        assert config.reasoning_effort is None

    def test_unset_max_tokens_stays_unset(self) -> None:
        """An unstated ceiling still defers to the settings ladder."""
        config = resolve_sampling(_identity_with())
        assert config.max_tokens is None


@pytest.mark.unit
class TestACallerStatingOneDialHasNotStatedTheRest:
    """Resolution merges field by field rather than choosing between two configs.

    Choosing would discard a whole binding on the strength of a single stated
    field, which is invisible precisely where it matters: a session that only
    cared about a token ceiling would lose the reasoning depth an operator
    bound, and every dial would still read correctly in isolation.
    """

    def test_the_binding_fills_what_the_caller_left_open(self) -> None:
        """A caller stating one dial still gets the binding for the others."""
        resolved = resolve_sampling(
            _identity_with(
                top_p=0.95,
                reasoning_effort=ReasoningEffort.HIGH,
                temperature=1.0,
            ),
            CompletionConfig(temperature=0.2, max_tokens=500),
        )

        assert resolved.max_tokens == 500
        assert resolved.temperature == pytest.approx(0.2)
        assert resolved.top_p == pytest.approx(0.95)
        assert resolved.reasoning_effort is ReasoningEffort.HIGH

    def test_a_stated_field_is_never_overwritten(self) -> None:
        """Everything the caller said survives, including a stated default.

        ``top_p`` defaults to 1.0 on the config, so a caller asking for 1.0
        cannot be told apart from one that said nothing by value alone. Read
        from ``model_fields_set``, it can: an explicit 1.0 is a request for the
        full distribution and outranks the binding's truncation.
        """
        resolved = resolve_sampling(
            _identity_with(top_p=0.5, temperature=1.5),
            CompletionConfig(temperature=0.1, top_p=1.0),
        )

        assert resolved.top_p == pytest.approx(1.0)
        assert resolved.temperature == pytest.approx(0.1)

    def test_a_binding_with_nothing_to_add_returns_the_caller_untouched(self) -> None:
        """No copy is made when the merge would change nothing."""
        requested = CompletionConfig(temperature=0.4, max_tokens=99)
        assert resolve_sampling(_identity_with(temperature=0.4), requested) is requested

    def test_a_prompt_caching_flag_the_binding_knows_nothing_about_survives(
        self,
    ) -> None:
        """The merge touches only the dials a binding can state."""
        resolved = resolve_sampling(
            _identity_with(reasoning_effort=ReasoningEffort.LOW),
            CompletionConfig(temperature=0.3, prompt_caching=True),
        )

        assert resolved.prompt_caching is True
        assert resolved.reasoning_effort is ReasoningEffort.LOW
