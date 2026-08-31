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

    def test_unset_top_p_reaches_the_request_unset(self) -> None:
        """An unstated threshold stays unstated all the way to the driver.

        Inventing one here, or letting a numeric config default stand in for
        it, would send a truncation the operator never chose and override
        whatever the provider applies by default.
        """
        config = resolve_sampling(_identity_with())
        assert config.top_p is None
        assert CompletionConfig().top_p is None

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


#: Built here, with the annotation supplying the element type, because a dict is
#: invariant in its value type: unpacked inline, each entry infers as
#: ``dict[str, ReasoningEffort]`` and is refused where ``dict[str, object]`` is
#: expected.
_REASONING_EFFORT_BINDINGS: tuple[dict[str, object], ...] = tuple(
    {"reasoning_effort": effort} for effort in ReasoningEffort
)


@pytest.mark.unit
class TestTheMergeCarriesNoValueTheTargetWouldRefuse:
    """The resolver copies bound dials across without revalidating them.

    ``model_copy(update=...)`` skips validation, so a value ``ModelConfig``
    accepts and ``CompletionConfig`` would refuse becomes a request nobody
    checked. What makes the copy safe is that every dial is bounded identically
    on both sides, which is a claim about two classes that can quietly stop
    being true when one is edited.

    Asserted behaviourally rather than by comparing declared bounds: three of
    the four dials carry numeric constraints, but ``reasoning_effort`` carries
    none at all (its constraint is the enum type), so a metadata comparison
    passes it by asserting that two empty mappings are equal and guards nothing.
    """

    #: Each dial at the edges of what a binding may hold, where a divergence
    #: between the two classes would first show. Every reasoning depth is drawn
    #: from the enum rather than listed, so a depth added later is covered
    #: without anyone remembering to add it here.
    _EXTREME_BINDINGS: tuple[dict[str, object], ...] = (
        {"temperature": 0.0},
        {"temperature": 2.0},
        {"top_p": 0.0},
        {"top_p": 1.0},
        {"max_tokens": 1},
        *_REASONING_EFFORT_BINDINGS,
    )

    @pytest.mark.parametrize("binding", _EXTREME_BINDINGS)
    def test_a_resolved_config_still_validates(
        self, binding: dict[str, object]
    ) -> None:
        """Re-validating the merge's output must not raise.

        This is the invariant the resolver's unvalidated copy rests on, stated
        as the question that matters: is what came out something the target
        class would itself have accepted.
        """
        resolved = resolve_sampling(_identity_with(**binding))

        assert CompletionConfig.model_validate(resolved.model_dump()) == resolved

    @pytest.mark.parametrize("binding", _EXTREME_BINDINGS)
    def test_a_resolved_config_still_validates_over_a_caller(
        self, binding: dict[str, object]
    ) -> None:
        """The same holds on the merge path, which copies onto a caller."""
        resolved = resolve_sampling(
            _identity_with(**binding), CompletionConfig(prompt_caching=True)
        )

        assert CompletionConfig.model_validate(resolved.model_dump()) == resolved
