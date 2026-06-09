"""Company structure and configuration models.

Department-internal structure models live in
:mod:`synthorg.core.company_departments`; cross-department handoff and
escalation models live in :mod:`synthorg.core.company_handoffs`.
"""

from collections import Counter
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.company_departments import Department
from synthorg.core.company_handoffs import EscalationPath, WorkflowHandoff
from synthorg.core.middleware_config import MiddlewareConfig
from synthorg.core.normalization import normalize_identifier
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.company import (
    COMPANY_BUDGET_UNDER_ALLOCATED,
    COMPANY_VALIDATION_ERROR,
)
from synthorg.organization.enums import CompanyType
from synthorg.security.autonomy.models import AutonomyConfig
from synthorg.security.timeout.config import ApprovalTimeoutConfig, WaitForeverConfig

logger = get_logger(__name__)


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
