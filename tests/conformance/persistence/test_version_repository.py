"""Conformance tests for the generic ``VersionRepository`` protocol.

Exercises the generic machinery via ``backend.workflow_versions`` since
that is the canonical concrete instantiation. If the generic layer works
for one entity, it works for all of them.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition, WorkflowNode
from synthorg.engine.workflow.enums import WorkflowNodeType, WorkflowType
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.versioning.hashing import compute_content_hash
from synthorg.versioning.models import VersionSnapshot

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)


_START_NODE = WorkflowNode(
    id=NotBlankStr("start"),
    type=WorkflowNodeType.START,
    label=NotBlankStr("Start"),
)
_END_NODE = WorkflowNode(
    id=NotBlankStr("end"),
    type=WorkflowNodeType.END,
    label=NotBlankStr("End"),
)


def _definition(
    definition_id: str = "wf-001",
    revision: int = 1,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=NotBlankStr(definition_id),
        name=NotBlankStr("Example"),
        description="",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=NotBlankStr("1.0.0"),
        nodes=(_START_NODE, _END_NODE),
        edges=(),
        created_by=NotBlankStr("alice"),
        created_at=_NOW,
        updated_at=_NOW,
        revision=revision,
    )


def _snapshot(
    entity_id: str = "wf-001",
    version: int = 1,
    definition: WorkflowDefinition | None = None,
    saved_at: datetime = _NOW,
) -> VersionSnapshot[WorkflowDefinition]:
    d = definition or _definition(definition_id=entity_id, revision=version)
    return VersionSnapshot(
        entity_id=NotBlankStr(entity_id),
        version=version,
        content_hash=NotBlankStr(compute_content_hash(d)),
        snapshot=d,
        saved_by=NotBlankStr("alice"),
        saved_at=saved_at,
    )


class TestVersionRepository:
    async def test_save_and_get_version(self, backend: PersistenceBackend) -> None:
        inserted = await backend.workflow_versions.save_version(_snapshot())
        assert inserted is True

        fetched = await backend.workflow_versions.get_version(
            NotBlankStr("wf-001"),
            1,
        )
        assert fetched is not None
        assert fetched.version == 1
        assert fetched.snapshot.id == "wf-001"

    async def test_save_version_is_idempotent(
        self, backend: PersistenceBackend
    ) -> None:
        snap = _snapshot()
        assert await backend.workflow_versions.save_version(snap) is True
        assert await backend.workflow_versions.save_version(snap) is False

    async def test_get_version_missing(self, backend: PersistenceBackend) -> None:
        fetched = await backend.workflow_versions.get_version(
            NotBlankStr("ghost"),
            1,
        )
        assert fetched is None

    async def test_get_latest_version(self, backend: PersistenceBackend) -> None:
        await backend.workflow_versions.save_version(
            _snapshot(version=1, saved_at=_NOW),
        )
        await backend.workflow_versions.save_version(
            _snapshot(
                version=2,
                definition=_definition(revision=2),
                saved_at=_NOW + timedelta(minutes=1),
            ),
        )
        await backend.workflow_versions.save_version(
            _snapshot(
                version=3,
                definition=_definition(revision=3),
                saved_at=_NOW + timedelta(minutes=2),
            ),
        )

        latest = await backend.workflow_versions.get_latest_version(
            NotBlankStr("wf-001"),
        )
        assert latest is not None
        assert latest.version == 3

    async def test_get_by_content_hash(self, backend: PersistenceBackend) -> None:
        d = _definition()
        hash_ = compute_content_hash(d)
        await backend.workflow_versions.save_version(_snapshot(definition=d))

        fetched = await backend.workflow_versions.get_by_content_hash(
            NotBlankStr("wf-001"),
            NotBlankStr(hash_),
        )
        assert fetched is not None
        assert fetched.content_hash == hash_

    async def test_list_versions_descending(self, backend: PersistenceBackend) -> None:
        await backend.workflow_versions.save_version(_snapshot(version=1))
        await backend.workflow_versions.save_version(
            _snapshot(version=2, definition=_definition(revision=2)),
        )

        rows = await backend.workflow_versions.list_versions(NotBlankStr("wf-001"))
        versions = [r.version for r in rows]
        assert versions == [2, 1]

    async def test_count_versions(self, backend: PersistenceBackend) -> None:
        await backend.workflow_versions.save_version(_snapshot(version=1))
        await backend.workflow_versions.save_version(
            _snapshot(version=2, definition=_definition(revision=2)),
        )

        count = await backend.workflow_versions.count_versions(NotBlankStr("wf-001"))
        assert count == 2

    async def test_delete_versions_for_entity(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.workflow_versions.save_version(_snapshot(version=1))
        await backend.workflow_versions.save_version(
            _snapshot(version=2, definition=_definition(revision=2)),
        )

        removed = await backend.workflow_versions.delete_versions_for_entity(
            NotBlankStr("wf-001"),
        )
        assert removed == 2
        assert (
            await backend.workflow_versions.get_latest_version(NotBlankStr("wf-001"))
            is None
        )
