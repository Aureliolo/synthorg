# module-kind: tests
"""Every agent the sweep staffs gets a per-response budget it can work in."""

import pytest

from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.staffing import (
    _BUILDER_ROLE,
    _RESPONSE_TOKEN_CEILING,
    SweepRoster,
    build_roster,
)
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.engine.response_budget import DEFAULT_AGENT_MAX_RESPONSE_TOKENS
from synthorg.engine.routing_policy.capability_policy import (
    CapabilityPolicy,
    ResolvedAgentCapabilityReader,
)
from synthorg.engine.routing_policy.config import CapabilityPolicyConfig
from synthorg.providers.routing.models import ResolvedModel

pytestmark = pytest.mark.unit

_EXECUTOR = ModelPair(
    provider="example-provider",
    model_id="example-capable-001",
    capability="capable",
    family="example-family-a",
)
_REVIEWER = ModelPair(
    provider="example-provider",
    model_id="example-expert-001",
    capability="expert",
    family="example-family-b",
)


class _UngradedResolver:
    """A catalogue that grades nothing, which is the placeholder pairs' case."""

    def resolve_for_pair(self, provider_name: str, ref: str) -> ResolvedModel | None:
        """Grade nothing.

        Returns:
            ``None``, so the roster's own claim is what selection reads.
        """
        del provider_name, ref
        return None


async def _roster() -> SweepRoster:
    """Register the roster a sweep would run on.

    Built through the public entry point rather than the identity minter it
    calls, so the assertion covers every agent the sweep actually staffs: the
    minter answering correctly says nothing about a role the roster builds by
    another route.

    Returns:
        The registered roster.
    """
    return await build_roster(
        executor=_EXECUTOR,
        reviewer=_REVIEWER,
        capability=CapabilityPolicy(
            config=CapabilityPolicyConfig(),
            reader=ResolvedAgentCapabilityReader(_UngradedResolver()),
        ),
    )


def _staffed(roster: SweepRoster) -> tuple[AgentIdentity, ...]:
    """Every identity the roster registered.

    Returns:
        The builders and the reviewers.
    """
    return (*roster.builders, *roster.reviewers)


async def test_staffed_agents_declare_their_own_ceiling() -> None:
    """The sweep pins the budget rather than inheriting whatever is configured.

    ``ModelConfig.max_tokens`` is the value that reaches the provider; the
    provider capability record is not read when building a request. A reasoning
    model spends the per-response budget on hidden reasoning before it can emit
    a tool call, so at the old flat 4096 seven of eight measured sessions
    emitted no tool call at all and were recorded as finished work. A recording
    has to be comparable across machines, so the sweep states its own figure
    instead of deferring to an operator setting that may differ.
    """
    staffed = _staffed(await _roster())
    unset = ModelConfig(provider="p", model_id="m").max_tokens

    assert unset is None
    assert staffed
    for identity in staffed:
        # Equality, not a lower bound. Any threshold below the sweep's own
        # figure is also satisfied by the value an UNSET binding resolves to,
        # so a roster that silently stopped declaring a ceiling would pass the
        # very test written to catch that.
        assert identity.model.max_tokens == _RESPONSE_TOKEN_CEILING
    assert _RESPONSE_TOKEN_CEILING != DEFAULT_AGENT_MAX_RESPONSE_TOKENS


async def test_staffed_agents_carry_the_bound_pair_unchanged() -> None:
    """The ceiling rides along with the pair, it does not replace it."""
    roster = await _roster()

    for builder in roster.builders:
        assert builder.model.provider == _EXECUTOR.provider
        assert builder.model.model_id == _EXECUTOR.model_id
    for reviewer in roster.reviewers:
        assert reviewer.model.provider == _REVIEWER.provider
        assert reviewer.model.model_id == _REVIEWER.model_id


async def test_each_role_samples_at_its_own_pairs_declared_values() -> None:
    """A pair's sampling reaches the agents bound to it, and only those.

    The two pairs a matrix binds are published with different values on
    different dials: one family exposes a temperature and a nucleus threshold
    and no graded reasoning parameter, the other exposes reasoning depth and
    documents that sampling has no effect while thinking is on. Collapsing them
    to one figure is guaranteed wrong for one of the two, so builders take the
    executor's values and reviewers the reviewer's.
    """
    roster = await build_roster(
        executor=_EXECUTOR.model_copy(
            update={"temperature": 1.0, "top_p": 0.95, "max_tokens": 131_072}
        ),
        reviewer=_REVIEWER.model_copy(
            update={
                "temperature": 0.6,
                "top_p": 0.5,
                "reasoning_effort": ReasoningEffort.HIGH,
                "max_tokens": 262_144,
            }
        ),
        capability=CapabilityPolicy(
            config=CapabilityPolicyConfig(),
            reader=ResolvedAgentCapabilityReader(_UngradedResolver()),
        ),
    )

    assert roster.builders
    assert roster.reviewers
    for builder in roster.builders:
        assert builder.model.temperature == pytest.approx(1.0)
        assert builder.model.top_p == pytest.approx(0.95)
        assert builder.model.reasoning_effort is None
        assert builder.model.max_tokens == 131_072
    for reviewer in roster.reviewers:
        assert reviewer.model.temperature == pytest.approx(0.6)
        assert reviewer.model.top_p == pytest.approx(0.5)
        assert reviewer.model.reasoning_effort is ReasoningEffort.HIGH
        assert reviewer.model.max_tokens == 262_144


async def test_an_undeclared_temperature_leaves_the_product_default() -> None:
    """Saying nothing inherits the product's value, not one this module picks.

    Copying that default here would diverge from it silently the day it moved.
    """
    roster = await _roster()
    default = ModelConfig(provider="p", model_id="m").temperature

    for identity in _staffed(roster):
        assert identity.model.temperature == pytest.approx(default)
        assert identity.model.top_p is None


async def test_the_planner_is_never_offered_the_reviewer_role() -> None:
    """The reviewers are staffed, and still not something to assign work to.

    This roster feeds the sweep's own planner, and the sweep is measuring what
    gating a merge is worth. A plan item owned by the judging role puts
    plan-level verification into BOTH arms, so the contrast is contaminated at
    source rather than measured: exactly what a live run recorded, at 19 of 102
    subtasks.
    """
    roster = await _roster()

    assert roster.reviewers
    assert COMPLETION_REVIEWER_ROLE_NAME in {
        str(agent.role) for agent in roster.reviewers
    }
    assert roster.roles == (_BUILDER_ROLE,)
