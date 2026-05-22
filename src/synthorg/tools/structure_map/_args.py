"""Typed argument model for the structure-map query tool."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class StructureMapFacet(StrEnum):
    """Which facet of the structure map to return."""

    MODULES = "modules"
    ENTRY_POINTS = "entry_points"
    TEST_SUITES = "test_suites"
    BUILD_FILES = "build_files"
    DEPENDENCIES = "dependencies"


_NameFilter = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class QueryStructureMapArgs(BaseModel):
    """Arguments for ``query_structure_map``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    facet: StructureMapFacet = Field(
        description="Which part of the structure map to list",
    )
    name_filter: _NameFilter | None = Field(
        default=None,
        description="Optional case-insensitive substring filter on entry path/name",
    )
