"""Conformance tests for ``SubworkflowRepository``."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import WorkflowNodeType, WorkflowType
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition, WorkflowNode
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)

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


def _subworkflow(
    *,
    subworkflow_id: str = "sub-001",
    version: str = "1.0.0",
    name: str = "Example subworkflow",
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=NotBlankStr(subworkflow_id),
        name=NotBlankStr(name),
        description="Reusable building block",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=NotBlankStr(version),
        is_subworkflow=True,
        nodes=(_START_NODE, _END_NODE),
        edges=(),
        created_by=NotBlankStr("alice"),
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestSubworkflowRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.subworkflows.save(_subworkflow())

        fetched = await backend.subworkflows.get(
            NotBlankStr("sub-001"),
            NotBlankStr("1.0.0"),
        )
        assert fetched is not None
        assert fetched.id == "sub-001"
        assert fetched.version == "1.0.0"

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        fetched = await backend.subworkflows.get(
            NotBlankStr("ghost"),
            NotBlankStr("1.0.0"),
        )
        assert fetched is None

    async def test_list_versions_orders_desc(self, backend: PersistenceBackend) -> None:
        # Mix lex-identical and lex-divergent pairs so a backend that
        # orders TEXT lexicographically (e.g. ``"1.10.0" < "1.2.0"``
        # under string sort) cannot pass; forces proper semver ordering.
        await backend.subworkflows.save(_subworkflow(version="1.0.0"))
        await backend.subworkflows.save(_subworkflow(version="1.1.0"))
        await backend.subworkflows.save(_subworkflow(version="1.2.0"))
        await backend.subworkflows.save(_subworkflow(version="1.10.0"))
        await backend.subworkflows.save(_subworkflow(version="2.0.0"))

        versions = await backend.subworkflows.list_versions(NotBlankStr("sub-001"))
        assert versions == ("2.0.0", "1.10.0", "1.2.0", "1.1.0", "1.0.0")

    async def test_list_versions_empty_for_unknown(
        self, backend: PersistenceBackend
    ) -> None:
        versions = await backend.subworkflows.list_versions(NotBlankStr("ghost"))
        assert versions == ()

    async def test_list_summaries(self, backend: PersistenceBackend) -> None:
        await backend.subworkflows.save(
            _subworkflow(subworkflow_id="a", version="1.0.0", name="Alpha"),
        )
        await backend.subworkflows.save(
            _subworkflow(subworkflow_id="b", version="2.0.0", name="Beta"),
        )

        summaries = await backend.subworkflows.list_summaries()
        ids = {s.subworkflow_id for s in summaries}
        assert {"a", "b"} <= ids

    async def test_list_summaries_respects_limit(
        self, backend: PersistenceBackend
    ) -> None:
        for i in range(5):
            await backend.subworkflows.save(
                _subworkflow(subworkflow_id=f"sub-lim-{i}", version="1.0.0"),
            )

        rows = await backend.subworkflows.list_summaries(limit=3)
        assert len(rows) == 3

    async def test_list_versions_respects_limit(
        self, backend: PersistenceBackend
    ) -> None:
        for v in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"):
            await backend.subworkflows.save(_subworkflow(version=v))

        versions = await backend.subworkflows.list_versions(
            NotBlankStr("sub-001"), limit=3
        )
        assert versions == ("1.4.0", "1.3.0", "1.2.0")

    async def test_search_by_name(self, backend: PersistenceBackend) -> None:
        await backend.subworkflows.save(
            _subworkflow(subworkflow_id="searchable", name="DataCleaner"),
        )

        hits = await backend.subworkflows.search(NotBlankStr("cleaner"))
        assert any(s.subworkflow_id == "searchable" for s in hits)

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.subworkflows.save(_subworkflow())

        deleted = await backend.subworkflows.delete(
            NotBlankStr("sub-001"),
            NotBlankStr("1.0.0"),
        )
        assert deleted is True
        assert (
            await backend.subworkflows.get(
                NotBlankStr("sub-001"),
                NotBlankStr("1.0.0"),
            )
            is None
        )

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        deleted = await backend.subworkflows.delete(
            NotBlankStr("ghost"),
            NotBlankStr("1.0.0"),
        )
        assert deleted is False

    async def test_delete_if_unreferenced_when_no_parents(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.subworkflows.save(_subworkflow())

        ok, parents = await backend.subworkflows.delete_if_unreferenced(
            NotBlankStr("sub-001"),
            NotBlankStr("1.0.0"),
        )
        assert ok is True
        assert parents == ()

    async def test_find_parents_empty_when_unreferenced(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.subworkflows.save(_subworkflow())

        parents = await backend.subworkflows.find_parents(NotBlankStr("sub-001"))
        assert parents == ()
