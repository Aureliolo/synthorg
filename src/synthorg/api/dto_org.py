"""Request DTOs for company, department, and agent mutation endpoints."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.company import Team  # noqa: TC001
from synthorg.core.enums import AutonomyLevel, SeniorityLevel
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.workflow.ceremony_policy import CeremonyPolicyConfig
from synthorg.organization.models import UpdateCompanyRequest

# -- Company -----------------------------------------------------------
#
# UpdateCompanyRequest is sourced from synthorg.organization.models so
# the domain service can validate the same input shape without
# importing from the API layer (audit-144).
__all__ = [
    "CreateAgentOrgRequest",
    "CreateDepartmentRequest",
    "ReorderAgentsRequest",
    "ReorderDepartmentsRequest",
    "UpdateAgentOrgRequest",
    "UpdateCompanyRequest",
    "UpdateDepartmentRequest",
]


# -- Departments -------------------------------------------------------


class CreateDepartmentRequest(BaseModel):
    """Request body for creating a new department."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    name: NotBlankStr = Field(max_length=128)
    head: NotBlankStr | None = None
    budget_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    autonomy_level: AutonomyLevel | None = None


class UpdateDepartmentRequest(BaseModel):
    """Partial update for an existing department."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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
        """Validate ceremony_policy against CeremonyPolicyConfig schema."""
        if v is not None:
            CeremonyPolicyConfig.model_validate(v)
        return v


class ReorderDepartmentsRequest(BaseModel):
    """Reorder departments -- must be an exact permutation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    department_names: tuple[NotBlankStr, ...] = Field(min_length=1)


# -- Agents ------------------------------------------------------------


class CreateAgentOrgRequest(BaseModel):
    """Request body for creating a new agent in the org config."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    name: NotBlankStr = Field(max_length=128)
    role: NotBlankStr = Field(max_length=128)
    department: NotBlankStr = Field(max_length=128)
    level: SeniorityLevel = SeniorityLevel.MID
    model_provider: NotBlankStr | None = None
    model_id: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_model_pair(self) -> CreateAgentOrgRequest:
        """Require both model_provider and model_id or neither."""
        if bool(self.model_provider) != bool(self.model_id):
            msg = "model_provider and model_id must both be provided or both omitted"
            raise ValueError(msg)
        return self


class UpdateAgentOrgRequest(BaseModel):
    """Partial update for an existing agent in the org config."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    name: NotBlankStr | None = Field(default=None, max_length=128)
    role: NotBlankStr | None = Field(default=None, max_length=128)
    department: NotBlankStr | None = Field(default=None, max_length=128)
    level: SeniorityLevel | None = None
    autonomy_level: AutonomyLevel | None = None
    model_provider: NotBlankStr | None = None
    model_id: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_model_pair(self) -> UpdateAgentOrgRequest:
        """Require both model_provider and model_id or neither."""
        if bool(self.model_provider) != bool(self.model_id):
            msg = "model_provider and model_id must both be provided or both omitted"
            raise ValueError(msg)
        return self


class ReorderAgentsRequest(BaseModel):
    """Reorder agents within a department -- must be an exact permutation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_names: tuple[NotBlankStr, ...] = Field(min_length=1)
