# module-kind: code
"""The roster a sweep runs as: builders, and the peers who judge them.

Two roles, and the second is the point. ``Completion Reviewer`` is an ordinary
roster role: the gate selects a holder per review, excludes the executor, and
escalates when nobody holds it. So the harness stands up real holders on the
reviewer's own binding rather than handing the gate an identity built in place,
which the product refuses for the same reason a reviewer that is not a peer
produces verdicts comparable to nothing.

Three reviewers rather than one. Selection excludes the executor and prefers a
holder who already worked the initiative, and a single-holder roster makes both
of those unobservable: every selection returns the same agent whatever the rule
did.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final
from uuid import UUID, uuid5

from evals.recursion_depth.manifest import ModelPair
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.context import roster_from_agents
from synthorg.engine.routing_policy.capability_policy import CapabilityPolicy
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.role_staffing import RoleStaffingService

#: Namespace for the sweep's agent ids, so a re-run reaches the same roster and
#: two runs of the same cell are attributable to the same actors.
_ROSTER_NAMESPACE: Final[UUID] = UUID("00000000-0000-4000-8000-00000000d000")

#: Every builder is a plain developer. The unit under test is the merge, so a
#: role-specific prompt shaping would be a second variable.
_BUILDER_ROLE: Final[str] = "Developer"
_DEPARTMENT: Final[str] = "Engineering"
_HIRING_DATE: Final[date] = date(2026, 1, 1)

#: Per-RESPONSE output ceiling for an agent whose pair declares none.
#:
#: A fallback now rather than the answer: `ModelPair.max_tokens` overrides it,
#: because how much room one response needs depends on how deeply that model
#: reasons before emitting content, which differs per pair and is exactly what
#: this figure cannot know.
#:
#: Declared rather than left to resolve, because the agent's own binding is
#: what reaches the provider: the capability record is read by nothing when a
#: request is built, and the sweep should not measure against whatever the
#: deployment's ``engine.agent_max_response_tokens`` happens to say.
#:
#: A small ceiling is fatal for a reasoning model, which spends the
#: per-response budget on hidden reasoning BEFORE it can emit content or a
#: tool call. On a development run of this harness (not a committed recording,
#: so the figures here are an observation rather than a result anyone can
#: re-read) seven of eight agent sessions burnt their whole 4096-token budget,
#: emitted no tool call at all, and were recorded as finished work, because a
#: turn with no tool call is how a session says it is done. Every model these
#: sweeps run against reports the `thinking` capability, so this is the normal
#: case rather than an edge one.
#:
#: A cap costs nothing unused: probed against the endpoint on the same run, a
#: request capped at 131072 returned 27 completion tokens. It permits a
#: response to finish, it does not lengthen one, so a truncated turn is spend
#: that buys nothing.
_RESPONSE_TOKEN_CEILING: Final[int] = 65_536

#: How many builders the roster carries. One per concurrent unit is not needed
#: (units run one at a time), but a plan that assigns work to two owners has to
#: find two, and a single-builder roster would silently collapse every plan to
#: one owner whatever it asked for.
BUILDER_COUNT: Final[int] = 3

#: How many holders the reviewer role carries.
REVIEWER_COUNT: Final[int] = 3


@dataclass(frozen=True)
class SweepRoster:
    """The agents one sweep runs as.

    Attributes:
        registry: The live roster the staffing service reads.
        staffing: Answers which reviewer judges each merge.
        builders: The agents units are dispatched to.
        reviewers: The holders of the reviewer role.
    """

    registry: AgentRegistryService
    staffing: RoleStaffingService
    builders: tuple[AgentIdentity, ...]
    reviewers: tuple[AgentIdentity, ...]
    unit_builders: tuple[AgentIdentity, ...] = ()
    #: The depth ``unit_builders`` is bound at, so an allocation can ask for
    #: a pool BY the depth it decided rather than by position. ``None`` when
    #: no second pool was built.
    unit_effort: ReasoningEffort | None = None

    @property
    def lead(self) -> AgentIdentity:
        """The builder that plans, and that owns a merge unless told otherwise.

        Returns:
            The first builder.
        """
        return self.builders[0]

    @property
    def leaf_builders(self) -> tuple[AgentIdentity, ...]:
        """The agents a UNIT is dispatched to.

        The same agents as everything else unless the matrix asked for units to
        reason at their own depth, which is the one published harness ablation
        with numbers behind it: holding a model fixed, reasoning hard at every
        phase scored WORSE than reasoning moderately at every phase (53.9%
        against 63.6%), and reasoning hard while planning and verifying but
        moderately while building beat both (66.5%). Implementation is mostly
        execution of a plan already understood; planning and assembly are where
        the depth is spent well.

        A separate POOL rather than a re-pointed binding, so an agent stays the
        fixed pair it was registered as.

        Returns:
            The unit pool when one was built, otherwise the ordinary builders.
        """
        return self.unit_builders or self.builders

    def pool_for(self, effort: ReasoningEffort | None) -> tuple[AgentIdentity, ...]:
        """The declared pool bound at *effort*.

        Asked BY depth rather than by role so the allocation that decided a
        unit's depth cannot reach a pool bound at some other one: ``None``
        is the executor's own pool, and the only other answer is the unit
        pool at the depth the matrix declared for it.

        Args:
            effort: The depth an allocation decided, or ``None`` for the
                executor's own.

        Returns:
            The agents bound at that depth.

        Raises:
            ValueError: No declared pool is bound at *effort*. Unreachable
                from :func:`session_limits_for`, which only ever names the
                manifest's own ``leaf_reasoning_effort``; reachable from a
                caller inventing a depth, which is the thing this refuses.
        """
        if effort is None:
            return self.builders
        if self.unit_builders and effort is self.unit_effort:
            return self.unit_builders
        msg = (
            f"no builder pool is bound at reasoning effort {effort.value!r}; "
            f"the roster declares {self.unit_effort} for its unit pool"
        )
        raise ValueError(msg)

    @property
    def roles(self) -> tuple[NotBlankStr, ...]:
        """The roles the planner may assign an owner from.

        Through the product's own answer rather than a set comprehension of this
        module's own, which is what let ``Completion Reviewer`` into the schema
        enum the sweep's planner is offered: the reviewers ARE staffed, so any
        rule reading the roster without asking what a role confers offers a
        judge as an executor.

        Returns:
            Each staffed role once, sorted, gate roles excluded.
        """
        return roster_from_agents((*self.builders, *self.reviewers))


def _identity(*, slug: str, name: str, role: str, pair: ModelPair) -> AgentIdentity:
    """Build one roster agent on an explicit binding.

    Returns:
        The identity.
    """
    # `temperature` alone is conditional because it alone is non-optional on
    # `ModelConfig`: passing `None` would be a validation error, where the
    # other three take `None` as the binding stating nothing. Omitting it also
    # lets `ModelConfig`'s own default stand rather than a copy of it here
    # that a later change to that default would silently leave behind.
    # `model_validate` rather than `ModelConfig(**fields)` because the values
    # are heterogeneous, and mypy strict refuses `dict[str, object]` unpacked
    # into a per-field-typed constructor.
    fields: dict[str, object] = {
        "provider": pair.provider,
        "model_id": pair.model_id,
        "capability": pair.capability,
        "top_p": pair.top_p,
        "reasoning_effort": pair.reasoning_effort,
        "max_tokens": pair.max_tokens or _RESPONSE_TOKEN_CEILING,
    }
    if pair.temperature is not None:
        fields["temperature"] = pair.temperature
    return AgentIdentity(
        id=uuid5(_ROSTER_NAMESPACE, slug),
        name=NotBlankStr(name),
        role=NotBlankStr(role),
        department=NotBlankStr(_DEPARTMENT),
        model=ModelConfig.model_validate(fields),
        hiring_date=_HIRING_DATE,
    )


async def build_roster(
    *,
    executor: ModelPair,
    reviewer: ModelPair,
    capability: CapabilityPolicy,
    leaf_effort: ReasoningEffort | None = None,
) -> SweepRoster:
    """Register the sweep's agents and wire the staffing service over them.

    Args:
        executor: The pair every builder is bound to.
        reviewer: The pair every reviewer is bound to.
        capability: The one capability policy, shared by selection and
            dispatch so a reviewer is measured against the bar the work
            itself was measured against.
        leaf_effort: Reasoning depth for the agents that BUILD units, or
            ``None`` to bind them exactly as every other builder. See
            :attr:`SweepRoster.leaf_builders`.

    Returns:
        The registered roster.
    """
    registry = AgentRegistryService()
    builders = tuple(
        _identity(
            slug=f"builder-{index}",
            name=f"Builder {index + 1}",
            role=_BUILDER_ROLE,
            pair=executor,
        )
        for index in range(BUILDER_COUNT)
    )
    # A SECOND POOL rather than a rewritten binding. An agent is a fixed
    # (role, model) unit and nothing in a loop may re-point the pair it was
    # bound to, so "cheaper reasoning while building" has to be a different
    # agent, which is what the product's own rule says work needing different
    # capability should reach for.
    leaf_builders = (
        ()
        if leaf_effort is None
        else tuple(
            _identity(
                slug=f"leaf-builder-{index}",
                name=f"Unit Builder {index + 1}",
                role=_BUILDER_ROLE,
                pair=executor.model_copy(update={"reasoning_effort": leaf_effort}),
            )
            for index in range(BUILDER_COUNT)
        )
    )
    reviewers = tuple(
        _identity(
            slug=f"reviewer-{index}",
            name=f"Reviewer {index + 1}",
            role=COMPLETION_REVIEWER_ROLE_NAME,
            pair=reviewer,
        )
        for index in range(REVIEWER_COUNT)
    )
    for agent in (*builders, *leaf_builders, *reviewers):
        await registry.register(agent)
    return SweepRoster(
        registry=registry,
        staffing=RoleStaffingService(registry=registry, capability=capability),
        builders=builders,
        reviewers=reviewers,
        unit_builders=leaf_builders,
        unit_effort=leaf_effort,
    )


__all__ = ["BUILDER_COUNT", "REVIEWER_COUNT", "SweepRoster", "build_roster"]
