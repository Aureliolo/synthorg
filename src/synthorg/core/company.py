"""Company structure and configuration models."""

import copy
from collections import Counter
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.middleware_config import MiddlewareConfig
from synthorg.core.normalization import normalize_identifier
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.company import (
    COMPANY_BUDGET_UNDER_ALLOCATED,
    COMPANY_VALIDATION_ERROR,
)
from synthorg.ontology.decorator import ontology_entity
from synthorg.organization.enums import CompanyType
from synthorg.security.autonomy.models import AutonomyConfig
from synthorg.security.timeout.config import ApprovalTimeoutConfig, WaitForeverConfig

logger = get_logger(__name__)

# ── Department internal structure models ─────────────────────────


def _identity_key(
    name: str,
    id_: str | None,
) -> tuple[str, str]:
    """Return ``(namespace, normalized_key)`` for identity comparison.

    Uses the explicit *id_* when provided, falling back to *name*.
    Namespacing prevents false collisions when an ID value happens
    to match a name from a different reporting line.

    Examples:
        ``_identity_key("Backend Developer", "backend-1")``
        returns ``("id", "backend-1")``.
        ``_identity_key("Backend Developer", None)``
        returns ``("name", "backend developer")``.
    """
    if id_ is not None:
        return ("id", normalize_identifier(id_))
    return ("name", normalize_identifier(name))


class ReportingLine(BaseModel):
    """Explicit reporting relationship within a department.

    Attributes:
        subordinate: Role name (or agent identifier) of the subordinate.
        supervisor: Role name (or agent identifier) of the supervisor.
        subordinate_id: Optional unique identifier for the subordinate.
            When multiple agents share the same role name, this
            disambiguates which agent is meant.  Any stable unique
            string is valid (e.g. the agent's ``merge_id`` in
            the template system).
        supervisor_id: Optional unique identifier for the supervisor.
            When multiple agents share the same role name, this
            disambiguates which agent is meant.  Any stable unique
            string is valid (e.g. the agent's ``merge_id`` in
            the template system).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    subordinate: NotBlankStr = Field(description="Subordinate role name or identifier")
    supervisor: NotBlankStr = Field(description="Supervisor role name or identifier")
    subordinate_id: NotBlankStr | None = Field(
        default=None,
        description="Optional unique identifier for the subordinate",
    )
    supervisor_id: NotBlankStr | None = Field(
        default=None,
        description="Optional unique identifier for the supervisor",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def subordinate_key(self) -> str:
        """Hierarchy lookup key: ``subordinate_id`` when set, else ``subordinate``.

        Unlike ``_identity_key()``, returns the raw value without
        case-folding or namespace tagging.
        """
        if self.subordinate_id is not None:
            return self.subordinate_id
        return self.subordinate

    @computed_field  # type: ignore[prop-decorator]
    @property
    def supervisor_key(self) -> str:
        """Hierarchy lookup key: ``supervisor_id`` when set, else ``supervisor``.

        Unlike ``_identity_key()``, returns the raw value without
        case-folding or namespace tagging.
        """
        if self.supervisor_id is not None:
            return self.supervisor_id
        return self.supervisor

    @model_validator(mode="after")
    def _validate_not_self_report(self) -> Self:
        """Reject self-reporting relationships.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the subordinate and supervisor resolve to the
                same identity (an agent reporting to itself).
        """
        sub_ns, sub_key = _identity_key(
            self.subordinate,
            self.subordinate_id,
        )
        sup_ns, sup_key = _identity_key(
            self.supervisor,
            self.supervisor_id,
        )
        if sub_ns != sup_ns:
            # Different namespaces (one identified by ID, the other
            # by name only).  We treat these as distinct because
            # comparing across namespaces would produce false
            # positives in legitimate configurations.
            return self
        if sub_key == sup_key:
            if self.subordinate_id is not None or self.supervisor_id is not None:
                msg = (
                    f"Agent cannot report to themselves: "
                    f"{self.subordinate!r}"
                    f" (id={self.subordinate_id!r})"
                    f" == {self.supervisor!r}"
                    f" (id={self.supervisor_id!r})"
                )
            else:
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action_type: NotBlankStr = Field(description="Action type for this chain")
    approvers: tuple[NotBlankStr, ...] = Field(description="Ordered approver names")
    min_approvals: int = Field(
        default=0,
        ge=0,
        description="Minimum approvals (0 = all required)",
    )

    @model_validator(mode="after")
    def _validate_approvers(self) -> Self:
        """Ensure approvers is non-empty, unique, and min_approvals is within bounds.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the approver list is empty, contains
                duplicates, or ``min_approvals`` exceeds the number of
                approvers.
        """
        if not self.approvers:
            msg = "Approval chain must have at least one approver"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        normalized = [normalize_identifier(a) for a in self.approvers]
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        """Ensure action_types are unique across approval chains.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If two approval chains target the same action
                type.
        """
        action_types = [c.action_type for c in self.approval_chains]
        if len(action_types) != len(set(action_types)):
            dupes = sorted(a for a, c in Counter(action_types).items() if c > 1)
            msg = f"Duplicate action types in approval chains: {dupes}"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


# ── Cross-department workflow models ─────────────────────────────


def _reject_same_department(from_dept: str, to_dept: str, label: str) -> None:
    """Reject cross-department models where from and to are the same.

    Raises:
        ValueError: If *from_dept* and *to_dept* resolve to the same
            department (case-insensitive).
    """
    if normalize_identifier(from_dept) == normalize_identifier(to_dept):
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    from_department: NotBlankStr = Field(description="Source department")
    to_department: NotBlankStr = Field(description="Target department")
    trigger: NotBlankStr = Field(description="Trigger condition")
    artifacts: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Artifacts passed during handoff",
    )

    @model_validator(mode="after")
    def _validate_different_departments(self) -> Self:
        """Reject handoffs within the same department.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the source and target departments are the same.
        """
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        """Reject escalations within the same department.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the source and target departments are the same.
        """
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Team name")
    lead: NotBlankStr = Field(description="Team lead agent name")
    members: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Team member agent names",
    )

    @model_validator(mode="after")
    def _validate_no_duplicate_members(self) -> Self:
        """Ensure no duplicate members (case-insensitive).

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If any member name appears more than once
                (case-insensitive).
        """
        normalized = [normalize_identifier(m) for m in self.members]
        if len(normalized) != len(set(normalized)):
            dup_keys = {m for m, c in Counter(normalized).items() if c > 1}
            dupes = sorted(
                m for m in self.members if normalize_identifier(m) in dup_keys
            )
            msg = f"Duplicate members in team {self.name!r}: {dupes}"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


@ontology_entity
class Department(BaseModel):
    """An organizational department.

    Department names may be standard values from
    :class:`~synthorg.organization.enums.DepartmentName` or custom names defined
    by the organization.

    Attributes:
        name: Department name (standard or custom).
        head: Department head role name (or agent identifier), or ``None``
            if the department has no designated head.  When absent,
            hierarchy resolution skips the team-lead-to-head link for
            this department.
        head_id: Optional unique identifier for the department head.
            When multiple agents share the same role name used in
            ``head``, this disambiguates which agent is meant.  Any
            stable unique string is valid (e.g. the agent's ``merge_id``
            in the template system).
        budget_percent: Percentage of company budget allocated (0-100).
        teams: Teams within this department.
        reporting_lines: Explicit reporting relationships.
        autonomy_level: Per-department autonomy level override
            (``None`` to inherit company default).
        policies: Department-level operational policies.
        ceremony_policy: Per-department ceremony scheduling policy
            override as a raw dict for YAML-level flexibility
            (templates pass raw dicts before full validation).
            ``None`` inherits the project-level policy.  Consumers
            construct ``CeremonyPolicyConfig`` from this dict when
            needed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Department name")
    head: NotBlankStr | None = Field(
        default=None,
        description="Department head role name or identifier",
    )
    head_id: NotBlankStr | None = Field(
        default=None,
        description="Optional unique identifier for the department head",
    )
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
    ceremony_policy: dict[str, Any] | None = Field(
        default=None,
        description="Per-department ceremony policy override",
    )

    @model_validator(mode="after")
    def _deepcopy_ceremony_policy(self) -> Self:
        """Defensive copy so callers cannot mutate the frozen model.

        Returns:
            The instance with ``ceremony_policy`` deep-copied so the
            caller's original dict cannot mutate the frozen model.
        """
        if self.ceremony_policy is not None:
            object.__setattr__(
                self,
                "ceremony_policy",
                copy.deepcopy(self.ceremony_policy),
            )
        return self

    @model_validator(mode="after")
    def _validate_head_id_requires_head(self) -> Self:
        """Reject head_id without a corresponding head.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``head_id`` is set while ``head`` is ``None``.
        """
        if self.head_id is not None and self.head is None:
            msg = (
                f"head_id {self.head_id!r} is set but head is None "
                f"for department {self.name!r}"
            )
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_team_names(self) -> Self:
        """Ensure no duplicate team names within a department (case-insensitive).

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If two teams share a name (case-insensitive)
                within the department.
        """
        names = [normalize_identifier(t.name) for t in self.teams]
        if len(names) != len(set(names)):
            dup_keys = {n for n, c in Counter(names).items() if c > 1}
            dupes = sorted(
                t.name for t in self.teams if normalize_identifier(t.name) in dup_keys
            )
            msg = f"Duplicate team names in department {self.name!r}: {dupes}"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_subordinates(self) -> Self:
        """Ensure no duplicate subordinates in reporting lines.

        Uses ``subordinate_id`` when present, falling back to
        ``subordinate`` name.  Keys are namespace-tagged to prevent
        false collisions between IDs and names.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the same subordinate appears in more than one
                reporting line.
        """
        subs = [
            _identity_key(r.subordinate, r.subordinate_id) for r in self.reporting_lines
        ]
        if len(subs) != len(set(subs)):
            dupes = sorted(
                f"{ns}:{key}" for (ns, key), c in Counter(subs).items() if c > 1
            )
            msg = (
                f"Duplicate subordinates in reporting lines "
                f"for department {self.name!r}: {dupes}"
            )
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


class CompanyConfig(BaseModel):
    """Company-wide configuration settings.

    Attributes:
        autonomy: Autonomy configuration (level + presets).
        approval_timeout: Timeout policy for pending approval items.
        budget_monthly: Monthly budget in the configured currency.
        communication_pattern: Default communication pattern name.
        tool_access_default: Default tool access for all agents.
        middleware: Agent and coordination middleware configuration.
        session_replay_on_start: When ``True``, replay the previous
            session from the event log on agent start (requires an
            ``EventReader`` and ``resume_execution_id``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    autonomy: AutonomyConfig = Field(
        default_factory=AutonomyConfig,
        description="Autonomy configuration (level + presets)",
    )
    approval_timeout: ApprovalTimeoutConfig = Field(
        default_factory=WaitForeverConfig,
        description="Timeout policy for pending approval items",
    )

    budget_monthly: float = Field(
        default=100.0,
        ge=0.0,
        description="Monthly budget in the configured currency",
    )
    communication_pattern: NotBlankStr = Field(
        default="hybrid",
        description="Default communication pattern",
    )
    tool_access_default: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Default tool access for all agents",
    )
    middleware: MiddlewareConfig = Field(
        default_factory=MiddlewareConfig,
        description="Agent and coordination middleware configuration",
    )
    session_replay_on_start: bool = Field(
        default=False,
        description="Replay session from event log on agent start",
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        """Ensure no duplicate entries in active_agents (case-insensitive).

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If any agent appears more than once in
                ``active_agents`` (case-insensitive).
        """
        normalized = [normalize_identifier(a) for a in self.active_agents]
        if len(normalized) != len(set(normalized)):
            dup_keys = {a for a, c in Counter(normalized).items() if c > 1}
            dupes = sorted(
                a for a in self.active_agents if normalize_identifier(a) in dup_keys
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        """Validate department names are unique and budgets do not exceed 100%.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If two departments share a name (case-insensitive)
                or the summed budget allocations exceed 100%.
        """
        # Unique department names (normalized for case-insensitive comparison)
        names = [normalize_identifier(d.name) for d in self.departments]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = f"Duplicate department names: {dupes}"
            logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)

        # Validate handoff/escalation references against declared departments
        known = set(names)
        for handoff in self.workflow_handoffs:
            for dept in (handoff.from_department, handoff.to_department):
                if normalize_identifier(dept) not in known:
                    msg = f"Workflow handoff references unknown department: {dept!r}"
                    logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
                    raise ValueError(msg)
        for escalation in self.escalation_paths:
            for dept in (escalation.from_department, escalation.to_department):
                if normalize_identifier(dept) not in known:
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
        if total > 0 and round(total, BUDGET_ROUNDING_PRECISION) < max_budget_percent:
            logger.info(
                COMPANY_BUDGET_UNDER_ALLOCATED,
                total_percent=round(total, BUDGET_ROUNDING_PRECISION),
                max_percent=max_budget_percent,
            )
        return self
