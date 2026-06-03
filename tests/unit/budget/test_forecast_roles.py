"""Unit tests for ``CompanyRoleSkeletonProvider``.

Verifies the pre-flight forecast's role skeleton is sourced from the live roster:
distinct roles, each mapped to the model the most agents in that role run, with
an empty company degrading to the single-role default.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from synthorg.budget.forecast_roles import (
    DEFAULT_ROLE_SKELETON,
    BriefRoleSkeleton,
    CompanyRoleSkeletonProvider,
)
from synthorg.hr.registry import AgentRegistryService
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def test_skeleton_rejects_assignment_role_not_in_roles() -> None:
    """A model assignment keyed on an unlisted role is a construction error."""
    with pytest.raises(ValidationError, match="not in roles"):
        BriefRoleSkeleton(
            roles=("backend developer",),
            model_assignments={"designer": "example-small-001"},
        )


def test_skeleton_accepts_assignment_subset_of_roles() -> None:
    """An assignment whose keys are a subset of roles is valid."""
    skeleton = BriefRoleSkeleton(
        roles=("backend developer", "designer"),
        model_assignments={"designer": "example-small-001"},
    )

    assert skeleton.model_assignments == {"designer": "example-small-001"}


def _agent(role: str, model_id: str) -> Any:
    return SimpleNamespace(role=role, model=SimpleNamespace(model_id=model_id))


def _provider(*agents: Any) -> CompanyRoleSkeletonProvider:
    registry = mock_of[AgentRegistryService](
        list_active=AsyncMock(return_value=tuple(agents)),
    )
    return CompanyRoleSkeletonProvider(registry=registry)


async def test_distinct_roles_with_representative_models() -> None:
    """Each role maps to the model the most agents in it run."""
    provider = _provider(
        _agent("Backend Developer", "example-large-001"),
        _agent("Backend Developer", "example-large-001"),
        _agent("Backend Developer", "example-medium-001"),
        _agent("Designer", "example-small-001"),
    )

    skeleton = await provider()

    assert skeleton.roles == ("Backend Developer", "Designer")
    assert skeleton.model_assignments["Backend Developer"] == "example-large-001"
    assert skeleton.model_assignments["Designer"] == "example-small-001"


async def test_empty_company_uses_default_skeleton() -> None:
    """An empty roster degrades to the single-role default."""
    provider = _provider()

    assert await provider() == DEFAULT_ROLE_SKELETON
