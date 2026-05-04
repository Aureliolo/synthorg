"""Unit tests for :class:`WorkflowRollbackService`.

The controller previously called ``repo.save(rolled_back)`` directly
on the workflow_definitions repository, then constructed a fresh
``VersioningService`` for the post-rollback snapshot.  The service
centralises both writes so audit logging cannot regress when a new
write path is added to the rollback contract.

These tests pin:

- ``rollback`` calls ``definition_repo.save`` exactly once.
- ``snapshot_if_changed`` is invoked on a ``VersioningService``
  constructed from the supplied ``version_repo``.
- A ``PersistenceError`` from the snapshot is swallowed (best-effort)
  so the rollback itself stays committed.
- A ``PersistenceVersionConflictError`` from ``definition_repo.save`` propagates
  to the caller (the controller catches it and returns 409).
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.services.workflow_rollback_service import WorkflowRollbackService
from synthorg.core.persistence_errors import (
    PersistenceError,
    PersistenceVersionConflictError,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.persistence.version_repo import VersionRepository
from synthorg.persistence.workflow_definition_repo import WorkflowDefinitionRepository

pytestmark = pytest.mark.unit


def _definition_stub() -> Any:
    """Build a stub WorkflowDefinition exposing the attrs the service touches.

    The service only reads ``id`` and ``revision`` off the
    definition; using a ``MagicMock(spec=WorkflowDefinition)`` keeps
    the test isolated from the (large) WorkflowDefinition constructor
    surface while still spec-locking attribute access against the real
    class so a future rename surfaces here.
    """
    stub = MagicMock(spec=WorkflowDefinition)
    stub.id = NotBlankStr("wfdef-1")
    stub.revision = 5
    return stub


class TestRollback:
    async def test_definition_save_happens_before_snapshot_attempt(
        self,
    ) -> None:
        """Save ordering invariant: live definition committed first.

        Records the await order of the two repo methods directly so
        the assertion catches a future refactor that flips
        ``definition_repo.save`` after the snapshot lookup -- a plain
        ``assert_awaited_once_with`` on each mock independently would
        accept that ordering reversal silently.
        """
        call_order: list[str] = []

        async def _record_save(_: object) -> None:
            call_order.append("save")

        async def _record_snapshot_lookup(*_args: object, **_kwargs: object) -> Any:
            call_order.append("snapshot")
            msg = "snapshot path skipped"
            raise PersistenceError(msg)

        definition_repo = AsyncMock(spec=WorkflowDefinitionRepository)
        definition_repo.save.side_effect = _record_save
        version_repo = AsyncMock(spec=VersionRepository)
        # ``get_latest_version`` raises so the snapshot branch
        # short-circuits without trying to construct the typed
        # VersionSnapshot[T] (which would require a real
        # WorkflowDefinition instance, not a stub) -- the swallow
        # happens inside the service, which is exactly the contract
        # we want to lean on here.
        version_repo.get_latest_version = AsyncMock(
            spec=VersionRepository.get_latest_version,
            side_effect=_record_snapshot_lookup,
        )
        service = WorkflowRollbackService(
            definition_repo=definition_repo,
            version_repo=version_repo,
        )
        rolled = _definition_stub()
        # No raise: rollback succeeds even when the snapshot path
        # short-circuits with a persistence error.
        await service.rollback(rolled, target_version=2, saved_by=NotBlankStr("user-1"))
        definition_repo.save.assert_awaited_once_with(rolled)
        version_repo.get_latest_version.assert_awaited()
        assert call_order == ["save", "snapshot"]

    async def test_swallows_snapshot_persistence_error(self) -> None:
        """A snapshot failure must not prevent the rollback success."""
        definition_repo = AsyncMock(spec=WorkflowDefinitionRepository)
        version_repo = AsyncMock(spec=VersionRepository)
        version_repo.get_latest_version = AsyncMock(
            spec=VersionRepository.get_latest_version,
            side_effect=PersistenceError("snap down"),
        )
        service = WorkflowRollbackService(
            definition_repo=definition_repo,
            version_repo=version_repo,
        )
        rolled = _definition_stub()
        # No exception bubbles -- the rollback is already persisted.
        await service.rollback(rolled, target_version=2, saved_by=NotBlankStr("user-1"))
        definition_repo.save.assert_awaited_once_with(rolled)

    async def test_propagates_version_conflict_from_definition_save(
        self,
    ) -> None:
        """Optimistic-concurrency mismatch surfaces to the controller."""
        definition_repo = AsyncMock(spec=WorkflowDefinitionRepository)
        definition_repo.save.side_effect = PersistenceVersionConflictError(
            "revision moved"
        )
        version_repo = AsyncMock(spec=VersionRepository)
        version_repo.get_latest_version = AsyncMock(
            spec=VersionRepository.get_latest_version,
        )
        service = WorkflowRollbackService(
            definition_repo=definition_repo,
            version_repo=version_repo,
        )
        with pytest.raises(PersistenceVersionConflictError):
            await service.rollback(
                _definition_stub(),
                target_version=2,
                saved_by=NotBlankStr("user-1"),
            )
        # Snapshot path must NOT be reached when the live save fails.
        version_repo.get_latest_version.assert_not_called()
