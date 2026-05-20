"""Built-in red-team :class:`AgentIdentity` factory.

The :class:`AgentIdentity` is the production seam for invoking the
agent via :class:`AgentEngine.run`; the factory binds the catalogued
``Red Team`` :class:`Role` to a caller-supplied :class:`ModelConfig`
so operators can choose the provider without forking the subsystem.

Tests cover the structural invariants (role name, department, level,
skill bag). Integration into the production wiring path runs via
:mod:`synthorg.workers.runtime_builder`.
"""

from datetime import UTC, datetime
from typing import Final

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.clock import Clock  # noqa: TC001
from synthorg.core.enums import DepartmentName, SeniorityLevel
from synthorg.core.role import Skill
from synthorg.core.role_catalog import RED_TEAM_ROLE_NAME, get_builtin_role
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.red_team import RED_TEAM_GATE_SKIPPED

logger = get_logger(__name__)

RED_TEAM_AGENT_NAME: Final[str] = "Red Team Skeptic"
"""Canonical display name for the built-in red-team agent."""


def _build_primary_skills() -> tuple[Skill, ...]:
    """Build the red-team's primary skill set.

    Skill IDs are stable for downstream routing dedup; descriptions
    are kept short so the agent identity stays compact in the prompt.
    """
    return (
        Skill(
            id="adversarial-analysis",
            name="Adversarial Analysis",
            description="Attack deliverables for unmet requirements and defects.",
            tags=("red-team", "review"),
        ),
        Skill(
            id="claim-grounding",
            name="Claim Grounding",
            description="Verify assertions trace to traceable sources.",
            tags=("grounding", "review"),
        ),
        Skill(
            id="security-review",
            name="Security Review",
            description="Identify input-validation and injection-class defects.",
            tags=("security", "review"),
        ),
        Skill(
            id="requirements-verification",
            name="Requirements Verification",
            description="Map acceptance criteria to deliverable evidence.",
            tags=("requirements", "review"),
        ),
    )


def build_red_team_agent_identity(
    *,
    model: ModelConfig,
    clock: Clock | None = None,
    name: NotBlankStr = RED_TEAM_AGENT_NAME,
) -> AgentIdentity:
    """Construct the built-in red-team :class:`AgentIdentity`.

    The factory looks the catalogued ``Red Team`` :class:`Role` up by
    name and copies its ``department`` and ``authority_level`` onto the
    agent identity, so a future Role edit propagates without changing
    this module.

    Args:
        model: Caller-supplied :class:`ModelConfig` (provider +
            model_id). The factory does not pick a default provider:
            operators wire this from the company's runtime config.
        clock: Optional clock injection point; defaults to
            ``datetime.now(UTC)`` for the agent's ``hiring_date``.
            Tests pass a :class:`FakeClock` to keep timestamps
            deterministic.
        name: Display name for the agent. Defaults to
            :data:`RED_TEAM_AGENT_NAME`.

    Returns:
        A frozen :class:`AgentIdentity` ready to feed into
        :class:`AgentEngine.run`.

    Raises:
        RuntimeError: If the catalogued role is missing (should never
            happen in normal use; surfaces as a hard configuration
            error rather than a silent fallback to a default role).
    """
    role = get_builtin_role(RED_TEAM_ROLE_NAME)
    if role is None:
        logger.warning(
            RED_TEAM_GATE_SKIPPED,
            reason="role_missing_from_catalog",
            role_name=RED_TEAM_ROLE_NAME,
            note=(
                "Built-in role missing; operators must check the "
                "BUILTIN_ROLES tuple in core/role_catalog.py."
            ),
        )
        msg = (
            f"Built-in role {RED_TEAM_ROLE_NAME!r} not found in catalog. "
            "Check core/role_catalog.py BUILTIN_ROLES tuple."
        )
        raise RuntimeError(msg)
    hiring_dt = clock.now() if clock is not None else datetime.now(UTC)
    return AgentIdentity(
        name=name,
        role=role.name,
        department=DepartmentName.QUALITY_ASSURANCE.value,
        level=SeniorityLevel.SENIOR,
        skills=SkillSet(primary=_build_primary_skills()),
        model=model,
        hiring_date=hiring_dt.date(),
    )
