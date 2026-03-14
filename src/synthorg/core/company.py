"""Company structure and configuration models."""

from collections import Counter
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.enums import AutonomyLevel, CompanyType
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.company import COMPANY_VALIDATION_ERROR
from synthorg.security.autonomy.models import AutonomyConfig
from synthorg.security.timeout.config import (
    ApprovalTimeoutConfig,
    WaitForeverConfig,
)

logger = get_logger(__name__)

# ── Department internal structure models ─────────────────────────


class ReportingLine(BaseModel):
    """Explicit reporting relationship within a department.

    Attributes:
        subordinate: Agent name of the subordinate.
        supervisor: Agent name of the supervisor.
    """

    model_config = ConfigDict(frozen=True)

    subordinate: NotBlankStr = Field(description="Subordinate agent name")
    supervisor: NotBlankStr = Field(description="Supervisor agent name")

    @model_validator(mode="after")
    def _validate_not_self_report(self) -> Self:
        """Reject self-reporting relationships."""
        if self.subordinate.strip().casefold() == self.supervisor.strip().casefold():
            msg = (
                f"Agent cannot report to themselves: "
                f"{self.subordinate!r} == {self.supervisor!r}"
            )
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


class ReviewRequirements(BaseModel):
    """Department review policy.

    Attributes:
        min_reviewers: Minimum number of reviewers required.
        required_reviewer_roles: Role names that must be among reviewers.
        self_review_allowed: Whether an agent can review their own work.
    """

    model_config = ConfigDict(frozen=True)

    min_reviewers: int = Field(
        default=1,
        ge=0,
        description="Minimum number of reviewers required",
    )
    required_reviewer_roles: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Role names that must be among reviewers",
    )
    self_review_allowed: bool = Field(
        default=False,
        description="Whether self-review is allowed",
    )


class ApprovalChain(BaseModel):
    """Ordered approver list for an action type.

    Attributes:
        action_type: Action type this chain applies to.
        approvers: Ordered tuple of approver agent names.
        min_approvals: Minimum approvals needed (0 = all approvers required).
    """

    model_config = ConfigDict(frozen=True)

    action_type: NotBlankStr = Field(description="Action type for this chain")
    approvers: tuple[NotBlankStr, ...] = Field(description="Ordered approver names")
    min_approvals: int = Field(
        default=0,
        ge=0,
        description="Minimum approvals (0 = all required)",
    )

    @model_validator(mode="after")
    def _validate_approvers(self) -> Self:
        """Ensure approvers is non-empty, unique, and min_approvals is within bounds."""
        if not self.approvers:
            msg = "Approval chain must have at least one approver"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        normalized = [a.strip().casefold() for a in self.approvers]
        if len(normalized) != len(set(normalized)):
            dupes = sorted(a for a, c in Counter(normalized).items() if c > 1)
            msg = f"Duplicate approvers in approval chain: {dupes}"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        if self.min_approvals > len(self.approvers):
            msg = (
                f"min_approvals ({self.min_approvals}) exceeds "
                f"number of approvers ({len(self.approvers)})"
            )
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


class DepartmentPolicies(BaseModel):
    """Department-level operational policies.

    Attributes:
        review_requirements: Review policy for this department.
        approval_chains: Approval chains for various action types.
    """

    model_config = ConfigDict(frozen=True)

    review_requirements: ReviewRequirements = Field(
        default_factory=ReviewRequirements,
        description="Review policy",
    )
    approval_chains: tuple[ApprovalChain, ...] = Field(
        default=(),
        description="Approval chains for action types",
    )

    @model_validator(mode="after")
    def _validate_unique_action_types(self) -> Self:
        """Ensure action_types are unique across approval chains."""
        action_types = [c.action_type for c in self.approval_chains]
        if len(action_types) != len(set(action_types)):
            dupes = sorted(a for a, c in Counter(action_types).items() if c > 1)
            msg = f"Duplicate action types in approval chains: {dupes}"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


# ── Cross-department workflow models ─────────────────────────────


def _reject_same_department(from_dept: str, to_dept: str, label: str) -> None:
    """Reject cross-department models where from and to are the same."""
    if from_dept.strip().casefold() == to_dept.strip().casefold():
        msg = (
            f"{label} must be between different departments: "
            f"{from_dept!r} == {to_dept!r}"
        )
        logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
        raise ValueError(msg)


class WorkflowHandoff(BaseModel):
    """Cross-department handoff definition.

    Attributes:
        from_department: Source department name.
        to_department: Target department name.
        trigger: Condition that triggers this handoff.
        artifacts: Artifacts passed during handoff.
    """

    model_config = ConfigDict(frozen=True)

    from_department: NotBlankStr = Field(description="Source department")
    to_department: NotBlankStr = Field(description="Target department")
    trigger: NotBlankStr = Field(description="Trigger condition")
    artifacts: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Artifacts passed during handoff",
    )

    @model_validator(mode="after")
    def _validate_different_departments(self) -> Self:
        """Reject handoffs within the same department."""
        _reject_same_department(
            self.from_department,
            self.to_department,
            "Handoff",
        )
        return self


class EscalationPath(BaseModel):
    """Cross-department escalation path.

    Attributes:
        from_department: Source department name.
        to_department: Target department name.
        condition: Condition that triggers escalation.
        priority_boost: Priority boost applied on escalation (0-3).
    """

    model_config = ConfigDict(frozen=True)

    from_department: NotBlankStr = Field(description="Source department")
    to_department: NotBlankStr = Field(description="Target department")
    condition: NotBlankStr = Field(description="Escalation condition")
    priority_boost: int = Field(
        default=1,
        ge=0,
        le=3,
        description="Priority boost on escalation (0-3)",
    )

    @model_validator(mode="after")
    def _validate_different_departments(self) -> Self:
        """Reject escalations within the same department."""
        _reject_same_department(
            self.from_department,
            self.to_department,
            "Escalation",
        )
        return self


class Team(BaseModel):
    """A team within a department.

    The ``lead`` is the team's manager. The ``lead`` may also appear in
    ``members`` if they are also an individual contributor.

    Attributes:
        name: Team name.
        lead: Team lead agent name (string reference).
        members: Team member agent names.
    """

    model_config = ConfigDict(frozen=True)

    name: NotBlankStr = Field(description="Team name")
    lead: NotBlankStr = Field(description="Team lead agent name")
    members: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Team member agent names",
    )

    @model_validator(mode="after")
    def _validate_no_duplicate_members(self) -> Self:
        """Ensure no duplicate members (case-insensitive)."""
        normalized = [m.strip().casefold() for m in self.members]
        if len(normalized) != len(set(normalized)):
            dup_keys = {m for m, c in Counter(normalized).items() if c > 1}
            dupes = sorted(m for m in self.members if m.strip().casefold() in dup_keys)
            msg = f"Duplicate members in team {self.name!r}: {dupes}"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


class Department(BaseModel):
    """An organizational department.

    Department names may be standard values from
    :class:`~synthorg.core.enums.DepartmentName` or custom names defined
    by the organization.

    Attributes:
        name: Department name (standard or custom).
        head: Department head agent name (string reference).
        budget_percent: Percentage of company budget allocated (0-100).
        teams: Teams within this department.
        reporting_lines: Explicit reporting relationships.
        policies: Department-level operational policies.
    """

    model_config = ConfigDict(frozen=True)

    name: NotBlankStr = Field(description="Department name")
    head: NotBlankStr = Field(description="Department head agent name")
    budget_percent: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Percentage of company budget allocated",
    )
    teams: tuple[Team, ...] = Field(
        default=(),
        description="Teams within this department",
    )
    reporting_lines: tuple[ReportingLine, ...] = Field(
        default=(),
        description="Explicit reporting relationships",
    )
    autonomy_level: AutonomyLevel | None = Field(
        default=None,
        description="Per-department autonomy level override (D6)",
    )
    policies: DepartmentPolicies = Field(
        default_factory=DepartmentPolicies,
        description="Department-level operational policies",
    )

    @model_validator(mode="after")
    def _validate_unique_team_names(self) -> Self:
        """Ensure no duplicate team names within a department (case-insensitive)."""
        names = [t.name.strip().casefold() for t in self.teams]
        if len(names) != len(set(names)):
            dup_keys = {n for n, c in Counter(names).items() if c > 1}
            dupes = sorted(
                t.name for t in self.teams if t.name.strip().casefold() in dup_keys
            )
            msg = f"Duplicate team names in department {self.name!r}: {dupes}"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_subordinates(self) -> Self:
        """Ensure no duplicate subordinates in reporting lines."""
        subs = [r.subordinate.strip().casefold() for r in self.reporting_lines]
        if len(subs) != len(set(subs)):
            dupes = sorted(s for s, c in Counter(subs).items() if c > 1)
            msg = (
                f"Duplicate subordinates in reporting lines "
                f"for department {self.name!r}: {dupes}"
            )
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


def _float_to_autonomy_level(value: float) -> AutonomyLevel:
    """Map a 0.0-1.0 float to an AutonomyLevel for backward compatibility.

    Thresholds: 0.0-0.24 → locked, 0.25-0.49 → supervised,
    0.5-0.79 → semi, 0.8-1.0 → full.
    """
    if value < 0.25:  # noqa: PLR2004
        return AutonomyLevel.LOCKED
    if value < 0.5:  # noqa: PLR2004
        return AutonomyLevel.SUPERVISED
    if value < 0.8:  # noqa: PLR2004
        return AutonomyLevel.SEMI
    return AutonomyLevel.FULL


class CompanyConfig(BaseModel):
    """Company-wide configuration settings.

    Attributes:
        autonomy: Autonomy configuration (level + presets).
            Accepts a bare float (0.0-1.0) for backward compatibility;
            the float is converted to an ``AutonomyConfig`` via a
            before-validator.
        budget_monthly: Monthly budget in USD.
        communication_pattern: Default communication pattern name.
        tool_access_default: Default tool access for all agents.
    """

    model_config = ConfigDict(frozen=True)

    autonomy: AutonomyConfig = Field(
        default_factory=AutonomyConfig,
        description="Autonomy configuration (level + presets)",
    )
    approval_timeout: ApprovalTimeoutConfig = Field(
        default_factory=WaitForeverConfig,
        description="Timeout policy for pending approval items",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_autonomy_float(cls, data: object) -> object:
        """Accept a bare float for autonomy and convert to AutonomyConfig."""
        if not isinstance(data, dict):
            return data
        raw = data.get("autonomy")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = float(raw)
            if not (0.0 <= value <= 1.0):
                msg = f"autonomy float must be 0.0-1.0, got {value}"
                raise ValueError(msg)
            level = _float_to_autonomy_level(value)
            return {**data, "autonomy": {"level": level.value}}
        return data

    budget_monthly: float = Field(
        default=100.0,
        ge=0.0,
        description="Monthly budget in USD",
    )
    communication_pattern: NotBlankStr = Field(
        default="hybrid",
        description="Default communication pattern",
    )
    tool_access_default: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Default tool access for all agents",
    )


class HRRegistry(BaseModel):
    """Human resources registry for the company.

    ``available_roles`` and ``hiring_queue`` intentionally allow duplicate
    entries to represent multiple openings for the same role or position.

    Attributes:
        active_agents: Currently active agent names (must be unique).
        available_roles: Roles available for hiring (duplicates allowed).
        hiring_queue: Roles in the hiring pipeline (duplicates allowed).
    """

    model_config = ConfigDict(frozen=True)

    active_agents: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Currently active agent names",
    )
    available_roles: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Roles available for hiring",
    )
    hiring_queue: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Roles in the hiring pipeline",
    )

    @model_validator(mode="after")
    def _validate_no_duplicate_active_agents(self) -> Self:
        """Ensure no duplicate entries in active_agents (case-insensitive)."""
        normalized = [a.strip().casefold() for a in self.active_agents]
        if len(normalized) != len(set(normalized)):
            dup_keys = {a for a, c in Counter(normalized).items() if c > 1}
            dupes = sorted(
                a for a in self.active_agents if a.strip().casefold() in dup_keys
            )
            msg = f"Duplicate entries in active_agents: {dupes}"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


class Company(BaseModel):
    """Top-level company entity.

    Validates that department names are unique and that budget allocations
    do not exceed 100%. The sum may be less than 100% to allow for an
    unallocated reserve.

    Attributes:
        id: Company identifier.
        name: Company name.
        type: Company template type.
        departments: Company departments.
        config: Company-wide configuration.
        hr_registry: HR registry.
        workflow_handoffs: Cross-department workflow handoffs.
        escalation_paths: Cross-department escalation paths.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4, description="Company identifier")
    name: NotBlankStr = Field(description="Company name")
    type: CompanyType = Field(
        default=CompanyType.CUSTOM,
        description="Company template type",
    )
    departments: tuple[Department, ...] = Field(
        default=(),
        description="Company departments",
    )
    config: CompanyConfig = Field(
        default_factory=CompanyConfig,
        description="Company-wide configuration",
    )
    hr_registry: HRRegistry = Field(
        default_factory=HRRegistry,
        description="HR registry",
    )
    workflow_handoffs: tuple[WorkflowHandoff, ...] = Field(
        default=(),
        description="Cross-department workflow handoffs",
    )
    escalation_paths: tuple[EscalationPath, ...] = Field(
        default=(),
        description="Cross-department escalation paths",
    )

    @model_validator(mode="after")
    def _validate_departments(self) -> Self:
        """Validate department names are unique and budgets do not exceed 100%."""
        # Unique department names (normalized for case-insensitive comparison)
        names = [d.name.strip().casefold() for d in self.departments]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = f"Duplicate department names: {dupes}"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)

        # Validate handoff/escalation references against declared departments
        known = set(names)
        for handoff in self.workflow_handoffs:
            for dept in (handoff.from_department, handoff.to_department):
                if dept.strip().casefold() not in known:
                    msg = f"Workflow handoff references unknown department: {dept!r}"
                    logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
                    raise ValueError(msg)
        for escalation in self.escalation_paths:
            for dept in (escalation.from_department, escalation.to_department):
                if dept.strip().casefold() not in known:
                    msg = f"Escalation path references unknown department: {dept!r}"
                    logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
                    raise ValueError(msg)

        # Budget sum
        max_budget_percent = 100.0
        total = sum(d.budget_percent for d in self.departments)
        if round(total, BUDGET_ROUNDING_PRECISION) > max_budget_percent:
            msg = (
                f"Department budget allocations sum to {total:.2f}%, "
                f"exceeding {max_budget_percent:.0f}%"
            )
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self
