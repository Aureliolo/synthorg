# module-kind: declarative
"""The query parameters the plan list accepts.

Declared beside each other because they are one surface: three optional
filters, each bounded by the same query-length cap, that the list route
combines. Kept out of the controller so the routes read as routes.
"""

from typing import Annotated

from litestar.params import QueryParameter

from synthorg.api.path_params import QUERY_MAX_LENGTH
from synthorg.core.types import NotBlankStr

PlanStatusFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by plan lifecycle status",
    ),
]

PlanProjectFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by project id",
    ),
]

PlanObjectiveFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by the charter/objective the plan serves",
    ),
]

__all__ = ["PlanObjectiveFilter", "PlanProjectFilter", "PlanStatusFilter"]
