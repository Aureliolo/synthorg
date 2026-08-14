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

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.project import Project
from synthorg.core.types import CapabilityLevel, NotBlankStr, capability_rank
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_STAFFING_NO_HOLDER,
    HR_STAFFING_REQUIREMENT_FLOORED,
    HR_STAFFING_SELECTED,
    HR_STAFFING_UNDER_CAPABILITY,
    HR_STAFFING_WIDENED,
)
from synthorg.persistence.project_protocol import ProjectRepository

logger = get_logger(__name__)

#: Rung an agent whose model carries no classification is judged at. An
#: unclassified pair must never outrank a classified one, or a model nobody
#: graded would win every selection by default.
_UNCLASSIFIED_RANK = capability_rank("basic")

StaffingSource = Literal["project_team", "org_wide"]
CapabilityFit = Literal["match", "higher", "lower"]


class RoleStaffingSelection(BaseModel):
    """The holder chosen for one piece of work, and why.

    Attributes:
        agent: The selected role holder.
        required_capability: What the work demanded, for the record.
        source: Whether the holder was already on the work's project team
            or was drawn from the wider org.
        capability_fit: How the holder's own bound model compares to the
            requirement.
        reason: Human-readable explanation, surfaced in logs and verdicts.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent: AgentIdentity = Field(description="The selected role holder")
    required_capability: CapabilityLevel = Field(
        description="Capability the reviewed work demanded",
    )
    source: StaffingSource = Field(description="Where the holder was drawn from")
    capability_fit: CapabilityFit = Field(
        description="How the holder's bound model compares to the requirement",
    )
    reason: NotBlankStr = Field(description="Why this holder was chosen")


def _capability_of(agent: AgentIdentity) -> int:
    """Return the rank of an agent's bound model capability."""
    capability = agent.model.capability
    return _UNCLASSIFIED_RANK if capability is None else capability_rank(capability)


def _on_project(agent: AgentIdentity, project: Project | None) -> bool:
    """Report whether *agent* is already staffed on *project*.

    Returns:
        ``True`` when the agent is on the project's team or is its lead.
    """
    if project is None:
        return False
    agent_id = str(agent.id)
    return agent_id in project.team or agent_id == project.lead


def _best_fit(
    candidates: tuple[AgentIdentity, ...],
    required_rank: int,
) -> tuple[AgentIdentity, CapabilityFit]:
    """Pick the candidate whose capability best fits *required_rank*.

    An exact match first, then the nearest rung above, then the nearest
    rung below. Going below is the last resort rather than a refusal,
    because a weaker reviewer is still a real independent reviewer and
    refusing would trade a real review for no review at all. Ties break on
    the agent id so the same pool always resolves the same way.

    Args:
        candidates: A non-empty pool of eligible holders.
        required_rank: The capability rank the work demands.

    Returns:
        The chosen candidate with how its capability fit.
    """
    ordered = sorted(candidates, key=lambda agent: str(agent.id))
    exact = [a for a in ordered if _capability_of(a) == required_rank]
    if exact:
        return exact[0], "match"
    above = [a for a in ordered if _capability_of(a) > required_rank]
    if above:
        return min(above, key=_capability_of), "higher"
    return max(ordered, key=_capability_of), "lower"


class RoleStaffingService:
    """Answers "who holds this role, and which of them fits this work".

    Args:
        registry: The live agent roster. Only ACTIVE holders are offered,
            because an agent the org has stood down is not an answer to a
            staffing question.
    """

    def __init__(self, *, registry: AgentRegistryProtocol) -> None:
        self._registry = registry

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
        if executor is None or executor.model.capability is None:
            return required_capability
        if capability_rank(executor.model.capability) <= capability_rank(
            required_capability
        ):
            return required_capability
        logger.info(
            HR_STAFFING_REQUIREMENT_FLOORED,
            role=str(role),
            executor_agent_id=str(executor_id),
            work_capability=required_capability,
            executor_capability=executor.model.capability,
        )
        return executor.model.capability

    async def select_holder(
        self,
        *,
        role: NotBlankStr,
        required_capability: CapabilityLevel,
        exclude_agent_id: NotBlankStr,
        project: Project | None,
    ) -> RoleStaffingSelection | None:
        """Choose the holder of *role* best suited to the work.

        The ladder, in order: holders already staffed on the work's project,
        then (logged) every holder in the org. Within whichever pool
        answers, capability fit decides: an exact match, else the nearest
        stronger holder, else the nearest weaker one.

        Args:
            role: The role a holder must carry.
            required_capability: What the reviewed work demands.
            exclude_agent_id: The executor, which may never judge its own
                work. Excluding it here is a convenience; the structural
                guarantee is the archive table's row-level CHECK.
            project: The reviewed work's project, when it has one.

        Returns:
            The selection, or ``None`` when nobody eligible holds the role.
        """
        required_capability = await self._floored_requirement(
            required_capability, executor_id=exclude_agent_id, role=role
        )
        holders = await self._registry.list_by_role(role)
        eligible = tuple(a for a in holders if str(a.id) != str(exclude_agent_id))
        if not eligible:
            logger.warning(
                HR_STAFFING_NO_HOLDER,
                role=str(role),
                holder_count=len(holders),
                excluded_executor=str(exclude_agent_id),
                project_id=str(project.id) if project is not None else None,
            )
            return None

        on_team = tuple(a for a in eligible if _on_project(a, project))
        source: StaffingSource = "project_team" if on_team else "org_wide"
        if not on_team and project is not None and project.team:
            # Only a project that HAS a team could have supplied one; saying
            # "widened" for a project with no team would name a narrowing
            # that never applied.
            logger.info(
                HR_STAFFING_WIDENED,
                role=str(role),
                project_id=str(project.id),
                reason="no_eligible_holder_on_project_team",
                org_wide_candidates=len(eligible),
            )
        agent, fit = _best_fit(
            on_team or eligible,
            capability_rank(required_capability),
        )

        if fit == "lower":
            logger.warning(
                HR_STAFFING_UNDER_CAPABILITY,
                role=str(role),
                agent_id=str(agent.id),
                required_capability=required_capability,
                holder_capability=agent.model.capability,
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
            holder_capability=agent.model.capability,
        )
        return RoleStaffingSelection(
            agent=agent,
            required_capability=required_capability,
            source=source,
            capability_fit=fit,
            reason=NotBlankStr(
                f"{agent.name} holds {role} ({source}, capability {fit} "
                f"against required {required_capability})"
            ),
        )


async def load_project_for_selection(
    project_repo: ProjectRepository | None,
    project_id: NotBlankStr | None,
    *,
    failure_event: str,
) -> Project | None:
    """Read the project a selection should prefer within, tolerating failure.

    Shared by both quality gates, which want the same thing from the same
    read. A failure costs only the on-team PREFERENCE: a gate role reaches
    every project regardless, so selection widens org-wide rather than
    blocking on a store that is momentarily unavailable.

    Args:
        project_repo: The project store, or ``None`` when unwired.
        project_id: The reviewed work's project, or ``None``.
        failure_event: The caller's observability event for a failed read,
            so the log names the gate that was selecting.

    Returns:
        The project, or ``None`` when there is none to read.

    Raises:
        asyncio.CancelledError: Propagated when the read is cancelled.
    """
    if project_repo is None or project_id is None:
        return None
    try:
        return await project_repo.get(project_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            failure_event,
            project_id=project_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="selecting org-wide instead of preferring the project's team",
        )
        return None
