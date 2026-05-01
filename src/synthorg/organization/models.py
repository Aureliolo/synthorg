"""Organization-layer domain models.

Holds the input model for company-level mutations so the
organization service no longer imports from the API layer
(audit-144 layer violation).
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import AutonomyLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


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
