# module-kind: service
"""Who holds a role, and which holder fits a piece of work.

HR owns the roster, so "which agent should review this?" is an HR
question rather than a gate's private business. Both quality gates (the
completion oracle's peer review and the adversarial red team) ask it here,
so there is one selection rule rather than two that drift.

The rule decides WHO handles the work. It never rewrites the
``(provider, model)`` pair an operator bound to an agent: capability is
matched by choosing a different agent, not by giving one agent a different
model.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.core.agent import AgentIdentity
from synthorg.core.capability_fit import CapabilityFit, best_by_fit
from synthorg.core.task_enums import Complexity, Stakes
from synthorg.core.types import CapabilityLevel, NotBlankStr, capability_rank
from synthorg.engine.routing_policy.capability_policy import CapabilityPolicy, rank_of
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HR_STAFFING_NO_HOLDER,
    HR_STAFFING_REQUIREMENT_FLOORED,
    HR_STAFFING_SELECTED,
    HR_STAFFING_UNDER_CAPABILITY,
    HR_STAFFING_WIDENED,
)

logger = get_logger(__name__)

StaffingSource = Literal["project_team", "org_wide"]


class RoleStaffingSelection(BaseModel):
    """The holder chosen for one piece of work, and why.

    Everything the explanation needs is a field, so the explanation is
    derived rather than stored: a settable ``reason`` is a second account
    of the same selection, free to say the holder came from the project
    team while ``source`` says otherwise.

    Attributes:
        agent: The selected role holder.
        role: The role it was selected to hold. Stored because the
            selection is the answer to a question about a role, and a
            reader holding one of these otherwise cannot say which.
        required_capability: What the work demanded, for the record.
        source: Whether the holder was already on the work's project team
            or was drawn from the wider org.
        capability_fit: How the holder's own bound model compares to the
            requirement.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent: AgentIdentity = Field(description="The selected role holder")
    role: NotBlankStr = Field(description="The role the holder was selected for")
    required_capability: CapabilityLevel = Field(
        description="Capability the reviewed work demanded",
    )
    source: StaffingSource = Field(description="Where the holder was drawn from")
    capability_fit: CapabilityFit = Field(
        description="How the holder's bound model compares to the requirement",
    )

    @computed_field
    @property
    def reason(self) -> str:
        """Why this holder was chosen, for logs and verdicts.

        Returns:
            A sentence naming the holder, the role, where it came from and
            how its capability compared.
        """
        return (
            f"{self.agent.name} holds {self.role} ({self.source}, capability "
            f"{self.capability_fit} against required {self.required_capability})"
        )


def _on_project(agent: AgentIdentity, contributors: frozenset[str]) -> bool:
    """Report whether *agent* already worked the initiative under review.

    Returns:
        ``True`` when the agent is among the initiative's contributors.
    """
    return str(agent.id) in contributors


def _best_fit(
    candidates: tuple[AgentIdentity, ...],
    required_rank: int,
    capability: CapabilityPolicy,
) -> tuple[AgentIdentity, CapabilityFit]:
    """Pick the holder whose capability best fits *required_rank*.

    Delegates the ordering to :func:`best_by_fit`, the one rule every
    selection in the org walks, so a reviewer is chosen exactly as a worker
    is. Going below is the last resort rather than a refusal here, because a
    weaker reviewer is still a real independent reviewer and refusing would
    trade a real review for no review at all. That holds only below the park
    floor: the caller passes sanctioned candidates alone, so at the stakes
    where dispatch refuses a weaker pair this never sees one.

    Args:
        candidates: A non-empty pool of eligible holders.
        required_rank: The capability rank the work demands.
        capability: The one policy that grades a bound pair, so a holder's
            rung here is the rung dispatch will read for the same pair.

    Returns:
        The chosen candidate with how its capability fit.

    Raises:
        AssertionError: If handed an empty pool, which the caller excludes.
    """
    chosen = best_by_fit(
        candidates,
        lambda agent: rank_of(capability.capability_of(agent.model)),
        required_rank,
        tie_break=lambda agent: str(agent.id),
    )
    assert chosen is not None  # noqa: S101  # caller checks the pool is non-empty
    return chosen


def _eligible_pool(
    eligible: tuple[AgentIdentity, ...],
    contributors: tuple[NotBlankStr, ...],
    *,
    role: NotBlankStr,
    project_id: NotBlankStr | None,
) -> tuple[tuple[AgentIdentity, ...], StaffingSource]:
    """Return the pool the fit is chosen from, and where it came from.

    The first rung of the ladder: holders who already worked the initiative,
    else every eligible holder in the org, with the widening logged so a
    cross-project reviewer is never a silent outcome.

    Args:
        eligible: Holders of the role, executor already excluded.
        contributors: Agent ids that have taken work on the reviewed
            initiative, derived from its tasks. Empty when the work has no
            project, or when nothing has been assigned on it yet.
        role: The role being staffed, for the log.
        project_id: The reviewed work's project, for the log.

    Returns:
        The pool to choose from and whether it was narrowed or widened.
    """
    on_team = frozenset(str(c) for c in contributors)
    narrowed = tuple(a for a in eligible if _on_project(a, on_team))
    if narrowed:
        return narrowed, "project_team"
    if on_team:
        # Only an initiative that HAS contributors could have supplied one;
        # saying "widened" for one nobody has worked yet would name a
        # narrowing that never applied.
        logger.info(
            HR_STAFFING_WIDENED,
            role=str(role),
            project_id=project_id,
            reason="no_eligible_holder_on_project_team",
            org_wide_candidates=len(eligible),
        )
    return eligible, "org_wide"


class RoleStaffingService:
    """Answers "who holds this role, and which of them fits this work".

    Args:
        registry: The live agent roster. Only ACTIVE holders are offered,
            because an agent the org has stood down is not an answer to a
            staffing question.
        capability: The one capability policy. It answers both halves of the
            question here (what rung the work demands, what rung a holder's
            bound pair runs at), so a reviewer is measured against exactly
            the bar dispatch will apply to the work it reviews.
    """

    __slots__ = ("_capability", "_registry")

    def __init__(
        self,
        *,
        registry: AgentRegistryProtocol,
        capability: CapabilityPolicy,
    ) -> None:
        self._registry = registry
        self._capability = capability

    async def has_holder(self, role: NotBlankStr) -> bool:
        """Return whether any ACTIVE agent holds *role* at all.

        Deliberately not :meth:`select_holder` with the work left out. That
        question is "who should judge THIS", and it needs stakes, complexity
        and an executor to exclude; asking it about no work at all would mean
        inventing all three. This is the prior question, "can this org staff
        the role at any stakes", which is the one worth asking before any work
        exists: a run filed against an org that answers no will park on its
        first deliverable whatever the work turns out to be.

        Args:
            role: The role to look for.

        Returns:
            ``True`` when at least one ACTIVE agent holds it.
        """
        return bool(await self._registry.list_by_role(role))

    async def _floored_requirement(
        self,
        required_capability: CapabilityLevel,
        *,
        executor_id: NotBlankStr,
        role: NotBlankStr,
    ) -> CapabilityLevel:
        """Raise the requirement to at least the executor's own capability.

        The work's stakes and complexity say what judging it demands, and for
        a subtask both are proposed by the agent that decomposed the work.
        Left alone, that lets the thing being judged choose a weaker judge:
        the fit ladder prefers an EXACT match, so a lower bar does not merely
        permit a weaker reviewer, it prefers one.

        A judge weaker than the agent whose work it judges is the one outcome
        the ladder should never produce, so the executor's own rung is a
        floor. Capability still follows the TASK; this only stops the task
        from bidding it below the author.

        Args:
            required_capability: What the work's stakes and complexity ask.
            executor_id: The agent whose work is under review.
            role: The role being staffed, for the log.

        Returns:
            The requirement, raised to the executor's rung when that is
            higher.
        """
        executor = await self._registry.get(executor_id)
        if executor is None:
            return required_capability
        executor_capability = self._capability.capability_of(executor.model)
        if executor_capability is None:
            return required_capability
        if capability_rank(executor_capability) <= capability_rank(required_capability):
            return required_capability
        logger.info(
            HR_STAFFING_REQUIREMENT_FLOORED,
            role=str(role),
            executor_agent_id=str(executor_id),
            work_capability=required_capability,
            executor_capability=executor_capability,
        )
        return executor_capability

    async def select_holder(
        self,
        *,
        role: NotBlankStr,
        stakes: Stakes,
        complexity: Complexity,
        exclude_agent_id: NotBlankStr,
        contributors: tuple[NotBlankStr, ...] = (),
        project_id: NotBlankStr | None = None,
    ) -> RoleStaffingSelection | None:
        """Choose the holder of *role* best suited to the work.

        The ladder, in order: holders who already worked the initiative,
        then (logged) every holder in the org. Within whichever pool
        answers, capability fit decides: an exact match, else the nearest
        stronger holder, else the nearest weaker one.

        The caller passes what the WORK is rather than a rung, so it cannot
        answer "what does judging this demand" differently from the policy
        every other selection reads. For the same reason a holder dispatch
        would refuse is dropped before the pools are formed, so this never
        hands back somebody the gate then turns away.

        Args:
            role: The role a holder must carry.
            stakes: How consequential the reviewed work is.
            complexity: The reviewed work's estimated complexity.
            exclude_agent_id: The executor, which may never judge its own
                work. Excluding it here is a convenience; the structural
                guarantee is the archive table's row-level CHECK.
            contributors: Agent ids that already took work on the reviewed
                initiative. Empty widens the pool org-wide.
            project_id: The reviewed work's project, for the log.

        Returns:
            The selection, or ``None`` when nobody holds the role, or nobody
            holding it may take work at these stakes. Both park the task on
            its staffing reason, which is what opens the hire.
        """
        required_capability = await self._floored_requirement(
            self._capability.required_for(stakes, complexity),
            executor_id=exclude_agent_id,
            role=role,
        )
        holders = await self._registry.list_by_role(role)
        eligible = tuple(a for a in holders if str(a.id) != str(exclude_agent_id))
        if not eligible:
            logger.warning(
                HR_STAFFING_NO_HOLDER,
                role=str(role),
                holder_count=len(holders),
                excluded_executor=str(exclude_agent_id),
                project_id=project_id,
                reason="no_eligible_holder",
            )
            return None

        # Judged on the bare stakes, which is the question dispatch asks of
        # the same pair. A holder this drops would be chosen here and then
        # refused there, and that refusal arrives as a dispatch fault rather
        # than the staffing park the hire sweep watches, so the role stays
        # unstaffed with nothing reaching the operator who could staff it.
        sanctioned = tuple(
            agent
            for agent in eligible
            if self._capability.judge(
                model=agent.model,
                stakes=stakes,
                complexity=complexity,
            ).sanctioned
        )
        if not sanctioned:
            logger.warning(
                HR_STAFFING_NO_HOLDER,
                role=str(role),
                holder_count=len(holders),
                eligible_count=len(eligible),
                required_capability=required_capability,
                stakes=stakes.value,
                excluded_executor=str(exclude_agent_id),
                project_id=project_id,
                reason="no_sanctioned_holder",
            )
            return None

        pool, source = _eligible_pool(
            sanctioned, contributors, role=role, project_id=project_id
        )
        agent, fit = _best_fit(
            pool, capability_rank(required_capability), self._capability
        )
        holder_capability = self._capability.capability_of(agent.model)

        if fit == "lower":
            logger.warning(
                HR_STAFFING_UNDER_CAPABILITY,
                role=str(role),
                agent_id=str(agent.id),
                required_capability=required_capability,
                holder_capability=holder_capability,
                note=(
                    "No holder at or above the capability this work demands; "
                    "reviewed by the strongest available holder instead."
                ),
            )
        logger.info(
            HR_STAFFING_SELECTED,
            role=str(role),
            agent_id=str(agent.id),
            source=source,
            capability_fit=fit,
            required_capability=required_capability,
            holder_capability=holder_capability,
        )
        return RoleStaffingSelection(
            agent=agent,
            role=role,
            required_capability=required_capability,
            source=source,
            capability_fit=fit,
        )
