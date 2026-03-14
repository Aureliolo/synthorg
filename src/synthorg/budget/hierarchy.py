"""Budget hierarchy models.

Implements the Budget Hierarchy section of the Operations design page:
Company to Department to Team, with percentage-based allocation at each
level.
"""

from collections import Counter
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.types import NotBlankStr  # noqa: TC001


class TeamBudget(BaseModel):
    """Budget allocation for a single team within a department.

    Attributes:
        team_name: Team name (string reference).
        budget_percent: Percent of department budget allocated to this team.
    """

    model_config = ConfigDict(frozen=True)

    team_name: NotBlankStr = Field(
        description="Team name",
    )
    budget_percent: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Percent of department budget",
    )


class DepartmentBudget(BaseModel):
    """Budget allocation for a department with nested team allocations.

    Team budget percentages may sum to less than 100% to allow for an
    unallocated reserve within the department.

    Attributes:
        department_name: Department name (string reference).
        budget_percent: Percent of company budget allocated to this department.
        teams: Team budget allocations within this department.
    """

    model_config = ConfigDict(frozen=True)

    department_name: NotBlankStr = Field(
        description="Department name",
    )
    budget_percent: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Percent of company budget",
    )
    teams: tuple[TeamBudget, ...] = Field(
        default=(),
        description="Team budget allocations",
    )

    @model_validator(mode="after")
    def _validate_unique_team_names(self) -> Self:
        """Ensure no duplicate team names within the department."""
        names = [t.team_name for t in self.teams]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = (
                f"Duplicate team names in department {self.department_name!r}: {dupes}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_team_budget_sum(self) -> Self:
        """Ensure team budget percentages do not exceed 100%."""
        max_budget_percent = 100.0
        total = sum(t.budget_percent for t in self.teams)
        if round(total, BUDGET_ROUNDING_PRECISION) > max_budget_percent:
            msg = (
                f"Team budget allocations in department "
                f"{self.department_name!r} sum to {total:.2f}%, "
                f"exceeding {max_budget_percent:.0f}%"
            )
            raise ValueError(msg)
        return self


class BudgetHierarchy(BaseModel):
    """Company-wide budget hierarchy.

    Maps the Company -> Department -> Team nesting from a budget
    allocation perspective (see Operations design page). Department budget
    percentages may sum to less than 100% to allow for an unallocated
    reserve at the company level.

    Attributes:
        total_monthly: Total company monthly budget in USD.
        departments: Department budget allocations.
    """

    model_config = ConfigDict(frozen=True)

    total_monthly: float = Field(
        ge=0.0,
        description="Total company monthly budget in USD",
    )
    departments: tuple[DepartmentBudget, ...] = Field(
        default=(),
        description="Department budget allocations",
    )

    @model_validator(mode="after")
    def _validate_unique_department_names(self) -> Self:
        """Ensure no duplicate department names."""
        names = [d.department_name for d in self.departments]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = f"Duplicate department names: {dupes}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_department_budget_sum(self) -> Self:
        """Ensure department budget percentages do not exceed 100%."""
        max_budget_percent = 100.0
        total = sum(d.budget_percent for d in self.departments)
        if round(total, BUDGET_ROUNDING_PRECISION) > max_budget_percent:
            msg = (
                f"Department budget allocations sum to {total:.2f}%, "
                f"exceeding {max_budget_percent:.0f}%"
            )
            raise ValueError(msg)
        return self
