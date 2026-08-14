# module-kind: code
"""Built-in completion-reviewer :class:`AgentIdentity` factory.

Binds the catalogued ``Completion Reviewer`` :class:`Role` to a
caller-supplied :class:`ModelConfig` so operators choose the reviewer's
provider without forking the subsystem. The identity is structurally
distinct from any executor: a stable, non-human-assignable agent id in the
Quality Assurance department, so the reviewer-is-distinct invariant holds
by construction, defence-in-depth checked again at gate time.
"""

from datetime import UTC, datetime
from typing import Final

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.clock import Clock
from synthorg.core.role import Skill
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME, get_builtin_role
from synthorg.core.types import NotBlankStr, stable_agent_id
from synthorg.engine.completion_oracle.errors import CompletionOracleRoleMissingError
from synthorg.observability import get_logger
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_GATE_BUILD_FAILED,
)

logger = get_logger(__name__)

COMPLETION_REVIEWER_AGENT_NAME: Final[str] = "Completion Reviewer"
"""Canonical display name for the built-in completion-reviewer agent."""


def _build_primary_skills() -> tuple[Skill, ...]:
    """Build the reviewer's primary skill set.

    Returns:
        The completion-reviewer agent's primary skills.
    """
    return (
        Skill(
            id="acceptance-verification",
            name="Acceptance Verification",
            description="Map acceptance criteria to deliverable evidence.",
            tags=("review", "requirements"),
        ),
        Skill(
            id="build-test-validation",
            name="Build & Test Validation",
            description="Build a deliverable and run its tests before approving.",
            tags=("review", "testing"),
        ),
        Skill(
            id="independent-review",
            name="Independent Review",
            description="Judge work impartially, distinct from its author.",
            tags=("review",),
        ),
    )


def build_completion_reviewer_identity(
    *,
    model: ModelConfig,
    clock: Clock | None = None,
    name: NotBlankStr = COMPLETION_REVIEWER_AGENT_NAME,
) -> AgentIdentity:
    """Construct the built-in completion-reviewer :class:`AgentIdentity`.

    Args:
        model: Caller-supplied :class:`ModelConfig`. Operators pin the
            reviewer's tier via the completion-oracle settings, never
            inheriting the executor's tier.
        clock: Optional clock injection for the agent's ``hiring_date``;
            defaults to ``datetime.now(UTC)``.
        name: Display name. Defaults to :data:`COMPLETION_REVIEWER_AGENT_NAME`.

    Returns:
        A frozen :class:`AgentIdentity` ready to feed into
        :class:`AgentEngine.run`.

    Raises:
        CompletionOracleRoleMissingError: If the catalogued role is missing.
    """
    role = get_builtin_role(COMPLETION_REVIEWER_ROLE_NAME)
    if role is None:
        logger.error(
            COMPLETION_ORACLE_GATE_BUILD_FAILED,
            reason="role_missing_from_catalog",
            role_name=COMPLETION_REVIEWER_ROLE_NAME,
            note=(
                "Built-in role missing; operators must check the BUILTIN_ROLES "
                "tuple in core/role_catalog.py."
            ),
        )
        msg = (
            f"Built-in role {COMPLETION_REVIEWER_ROLE_NAME!r} not found in "
            "catalog. Check core/role_catalog.py BUILTIN_ROLES tuple."
        )
        raise CompletionOracleRoleMissingError(msg)
    hiring_dt = clock.now() if clock is not None else datetime.now(UTC)
    return AgentIdentity(
        id=stable_agent_id(name),
        name=name,
        role=role.name,
        department=role.department.value,
        skills=SkillSet(primary=_build_primary_skills()),
        model=model,
        hiring_date=hiring_dt.date(),
    )
