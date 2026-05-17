"""Cross-repo conformance: ``list_items`` pagination validation.

The WP-1 rollout routes every paginated repository ``list_items`` through
``validate_pagination_args``, which rejects out-of-range pagination with
a domain ``QueryError`` rather than returning ``()`` or hitting the DB.
This module exercises that single reject branch across every touched
repo and both backends in one parametrized sweep -- the highest-leverage
patch-coverage path since the branch is copy-pasted ~40 times.

Each parametrized case calls ``backend.<accessor>.list_items(...)`` with
an invalid ``limit`` / ``offset`` and asserts ``QueryError``. Repos whose
``list_items`` legitimately does not paginate are simply not listed here.
"""

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

# Repo accessors on ``PersistenceBackend`` whose ``list_items`` takes
# keyword-only ``limit`` / ``offset`` and routes through
# ``validate_pagination_args``. Bespoke-signature or non-paginated
# surfaces are intentionally excluded.
_PAGINATED_ACCESSORS = (
    "tasks",
    "parked_contexts",
    "users",
    "api_keys",
    "agent_states",
    "artifacts",
    "projects",
    "custom_presets",
    "workflow_definitions",
    "subworkflows",
    "risk_overrides",
    "ssrf_violations",
    "ceremony_scheduler_state",
    "meeting_cooldown",
    "tracked_containers",
    "connections",
    "principle_overrides",
    "training_plans",
    "training_results",
    "custom_rules",
    "sessions",
    "mcp_installations",
    "ontology_entities",
)

_INVALID_PAGINATION = (
    pytest.param(0, 0, id="limit-zero"),
    pytest.param(-1, 0, id="limit-negative"),
    pytest.param(1, -1, id="offset-negative"),
)


class TestListItemsPaginationValidationConformance:
    @pytest.mark.parametrize("accessor", _PAGINATED_ACCESSORS)
    @pytest.mark.parametrize(("limit", "offset"), _INVALID_PAGINATION)
    async def test_list_items_rejects_invalid_pagination(
        self,
        backend: PersistenceBackend,
        accessor: str,
        limit: int,
        offset: int,
    ) -> None:
        repo = getattr(backend, accessor)
        with pytest.raises(QueryError):
            await repo.list_items(limit=limit, offset=offset)

    @pytest.mark.parametrize("accessor", _PAGINATED_ACCESSORS)
    async def test_list_items_empty_with_valid_pagination(
        self,
        backend: PersistenceBackend,
        accessor: str,
    ) -> None:
        # Valid window on an empty table returns ``()`` -- exercises the
        # post-validation happy path + zero-row deserialize branch.
        repo = getattr(backend, accessor)
        assert await repo.list_items(limit=10, offset=0) == ()
