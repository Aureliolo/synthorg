# module-kind: tests
"""Every agent the sweep staffs gets a per-response budget it can work in."""

import pytest

from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.staffing import SweepRoster, build_roster
from synthorg.core.agent import AgentIdentity, ModelConfig
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
        ceiling = identity.model.max_tokens
        assert ceiling is not None
        assert ceiling > DEFAULT_AGENT_MAX_RESPONSE_TOKENS // 2


async def test_staffed_agents_carry_the_bound_pair_unchanged() -> None:
    """The ceiling rides along with the pair, it does not replace it."""
    roster = await _roster()

    for builder in roster.builders:
        assert builder.model.provider == _EXECUTOR.provider
        assert builder.model.model_id == _EXECUTOR.model_id
    for reviewer in roster.reviewers:
        assert reviewer.model.provider == _REVIEWER.provider
        assert reviewer.model.model_id == _REVIEWER.model_id
