"""Organization-layer domain models.

Holds the input models for company, department, and agent mutations so
the organization service layer can validate the same input shapes
without importing from the API layer. The ``synthorg.api.dto_org``
module re-exports these for HTTP controllers.
"""

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from synthorg.core.company import Team
from synthorg.core.enums import AutonomyLevel
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.ceremony_policy import CeremonyPolicyConfig
from synthorg.hr.seniority import SeniorityLevel


class UpdateCompanyRequest(BaseModel):
    """Partial update for company-level settings.

    Lives in the organization domain layer so the service can
    validate input without importing from ``synthorg.api.dto_org``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    company_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the company.",
    )
    autonomy_level: AutonomyLevel | None = Field(
        default=None,
        description="Org-wide autonomy level (full, semi, supervised, locked).",
    )
    budget_monthly: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Monthly budget cap for the company in the operator's configured "
            "currency; set to 0 to disable enforcement."
        ),
    )
    communication_pattern: NotBlankStr | None = Field(
        default=None,
        description="Communication strategy or pattern identifier.",
    )


class CreateDepartmentRequest(BaseModel):
    """Request body for creating a new department."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(max_length=128)
    head: NotBlankStr | None = None
    budget_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    autonomy_level: AutonomyLevel | None = None


class UpdateDepartmentRequest(BaseModel):
    """Partial update for an existing department."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    head: NotBlankStr | None = None
    budget_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    autonomy_level: AutonomyLevel | None = None
    teams: tuple[Team, ...] | None = Field(default=None, max_length=64)
    # Stored as a raw dict at the domain level for YAML-level flexibility
    # (see ``Department.ceremony_policy``); validated against
    # ``CeremonyPolicyConfig`` but not coerced to the typed model.
    ceremony_policy: dict[str, object] | None = None

    @field_validator("ceremony_policy", mode="before")
    @classmethod
    def _validate_ceremony_policy(
        cls, v: dict[str, object] | None
    ) -> dict[str, object] | None:
        """Validate ceremony_policy against CeremonyPolicyConfig schema.

        Returns:
            The input value unchanged once validated (``None`` passes
            through; a dict is checked against ``CeremonyPolicyConfig``
            but not coerced to the typed model).
        """
        if v is not None:
            CeremonyPolicyConfig.model_validate(v)
        return v


class ReorderDepartmentsRequest(BaseModel):
    """Reorder departments -- must be an exact permutation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    department_names: tuple[NotBlankStr, ...] = Field(min_length=1)


class CreateAgentOrgRequest(BaseModel):
    """Request body for creating a new agent in the org config."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(max_length=128)
    role: NotBlankStr = Field(max_length=128)
    department: NotBlankStr = Field(max_length=128)
    level: SeniorityLevel = SeniorityLevel.MID
    model_provider: NotBlankStr | None = None
    model_id: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_model_pair(self) -> CreateAgentOrgRequest:
        """Require both model_provider and model_id or neither.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When exactly one of ``model_provider`` /
                ``model_id`` is set.
        """
        if bool(self.model_provider) != bool(self.model_id):
            msg = "model_provider and model_id must both be provided or both omitted"
            raise ValueError(msg)
        return self


class UpdateAgentOrgRequest(BaseModel):
    """Partial update for an existing agent in the org config."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr | None = Field(default=None, max_length=128)
    role: NotBlankStr | None = Field(default=None, max_length=128)
    department: NotBlankStr | None = Field(default=None, max_length=128)
    level: SeniorityLevel | None = None
    autonomy_level: AutonomyLevel | None = None
    model_provider: NotBlankStr | None = None
    model_id: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_model_pair(self) -> UpdateAgentOrgRequest:
        """Require both model_provider and model_id or neither.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When exactly one of ``model_provider`` /
                ``model_id`` is set.
        """
        if bool(self.model_provider) != bool(self.model_id):
            msg = "model_provider and model_id must both be provided or both omitted"
            raise ValueError(msg)
        return self


class ReorderAgentsRequest(BaseModel):
    """Reorder agents within a department -- must be an exact permutation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_names: tuple[NotBlankStr, ...] = Field(min_length=1)
