# module-kind: code
"""Company role-skeleton provider for the pre-flight forecast gate.

At the work-entry seam a brief has not yet been decomposed into concrete
role assignments, so the :class:`~synthorg.engine.pipeline.forecast_gate.ForecastGate`
historically forecast over a single ``"default"`` role -- which systematically
under-estimates a multi-agent run. This provider sources the company's live role
roster (distinct roles + the model each role predominantly runs) from the
registry so the forecast spans every role that could participate, with each
role's tier feeding the forecaster's per-role prior. It over- rather than
under-estimates (not every role touches every brief), which is the safe side for
an operator-approved ceiling.
"""

from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger

logger = get_logger(__name__)


class BriefRoleSkeleton(BaseModel):
    """A role skeleton + per-role model assignment for a pre-flight forecast.

    ``roles`` is the ordered set of role ids the forecast spans;
    ``model_assignments`` maps each role to the model id it predominantly runs
    so the forecaster derives the right per-role tier prior.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    roles: tuple[NotBlankStr, ...] = Field(min_length=1)
    model_assignments: Mapping[NotBlankStr, NotBlankStr] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _assignments_subset_of_roles(self) -> Self:
        """Reject assignments naming a role absent from ``roles``.

        A model assignment keyed on a role the skeleton does not span is
        a construction error: the forecaster would never look it up, so
        it silently does nothing. Failing fast keeps the two fields
        consistent.

        Returns:
            The validated skeleton.

        Raises:
            ValueError: If ``model_assignments`` keys are not a subset of
                ``roles``.
        """
        extra = self.model_assignments.keys() - set(self.roles)
        if extra:
            msg = f"model_assignments names roles not in roles: {sorted(extra)!r}"
            raise ValueError(msg)
        return self


#: The single-role fallback used when no roster is available (an empty company,
#: or a registry absent at wiring time): a coarse estimate over the brief text.
DEFAULT_ROLE_SKELETON: BriefRoleSkeleton = BriefRoleSkeleton(
    roles=(NotBlankStr("default"),)
)

RoleSkeletonProvider = Callable[[], Awaitable[BriefRoleSkeleton]]
"""Async provider of the brief role skeleton for a forecast."""


class CompanyRoleSkeletonProvider:
    """Build a :class:`BriefRoleSkeleton` from the live company roster.

    Satisfies :data:`RoleSkeletonProvider`. Groups active agents by role, picks
    each role's representative model (the one the most agents in that role run),
    and returns the distinct roles + assignments. An empty roster yields the
    single-role default so the forecast still produces a coarse estimate.

    Args:
        registry: Live agent registry (active agents -> role + model).
    """

    def __init__(self, *, registry: AgentRegistryService) -> None:
        self._registry = registry

    async def __call__(self) -> BriefRoleSkeleton:
        """Return the company role skeleton from the live roster.

        Returns:
            The distinct active roles + per-role representative model, or the
            single-role default when no agents are active.
        """
        agents = await self._registry.list_active()
        if not agents:
            return DEFAULT_ROLE_SKELETON
        models_by_role: dict[str, Counter[str]] = defaultdict(Counter)
        for agent in agents:
            models_by_role[str(agent.role)][agent.model.model_id] += 1
        roles = tuple(NotBlankStr(role) for role in sorted(models_by_role))
        assignments = {
            NotBlankStr(role): NotBlankStr(counts.most_common(1)[0][0])
            for role, counts in models_by_role.items()
        }
        return BriefRoleSkeleton(roles=roles, model_assignments=assignments)


__all__ = [
    "DEFAULT_ROLE_SKELETON",
    "BriefRoleSkeleton",
    "CompanyRoleSkeletonProvider",
    "RoleSkeletonProvider",
]
