"""Tests for :class:`SubworkflowRegistry`."""

import copy
from datetime import UTC, datetime

import pytest

from synthorg.core.enums import (
    WorkflowEdgeType,
    WorkflowNodeType,
    WorkflowType,
)
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.engine.errors import (
    SubworkflowIOError,
    SubworkflowNotFoundError,
)
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.subworkflow_models import (
    ParentReference,
    SubworkflowSummary,
)
from synthorg.engine.workflow.subworkflow_registry import SubworkflowRegistry
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.subworkflow_protocol import SubworkflowRepository

_DEFAULT_TS = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _make_subworkflow(  # noqa: PLR0913
    *,
    subworkflow_id: str = "sub-finance-close",
    version: str = "1.0.0",
    name: str = "Finance Close",
    is_subworkflow: bool = True,
    inputs: tuple[WorkflowIODeclaration, ...] = (),
    outputs: tuple[WorkflowIODeclaration, ...] = (),
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=subworkflow_id,
        name=name,
        description="Finance close",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=version,
        inputs=inputs,
        outputs=outputs,
        is_subworkflow=is_subworkflow,
        nodes=(
            WorkflowNode(id="s", type=WorkflowNodeType.START, label="Start"),
            WorkflowNode(
                id="t",
                type=WorkflowNodeType.TASK,
                label="Close",
                config={"title": "Close", "task_type": "admin"},
            ),
            WorkflowNode(id="e", type=WorkflowNodeType.END, label="End"),
        ),
        edges=(
            WorkflowEdge(
                id="e1",
                source_node_id="s",
                target_node_id="t",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
            WorkflowEdge(
                id="e2",
                source_node_id="t",
                target_node_id="e",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
        ),
        created_by="test-user",
        created_at=_DEFAULT_TS,
        updated_at=_DEFAULT_TS,
    )


class FakeSubworkflowRepository(SubworkflowRepository):
    """In-memory fake repository for registry unit tests."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], WorkflowDefinition] = {}
        self._parents: dict[str, list[ParentReference]] = {}

    async def save(self, definition: WorkflowDefinition) -> None:
        key = (definition.id, definition.version)
        if key in self._rows:
            msg = f"Duplicate {key}"
            raise DuplicateRecordError(msg)
        self._rows[key] = copy.deepcopy(definition)

    async def get(
        self,
        entity_id: tuple[str, str],
    ) -> WorkflowDefinition | None:
        row = self._rows.get(entity_id)
        return copy.deepcopy(row) if row is not None else None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        items = sorted(self._rows.items())
        return tuple(
            copy.deepcopy(definition)
            for _, definition in items[offset : offset + limit]
        )

    async def list_versions(
        self,
        subworkflow_id: str,
        *,
        limit: int = 100,
    ) -> tuple[str, ...]:
        from packaging.version import Version

        versions = [v for (sid, v) in self._rows if sid == subworkflow_id]
        versions.sort(key=Version, reverse=True)
        return tuple(versions[:limit])

    async def list_summaries(
        self,
        *,
        limit: int = 100,
    ) -> tuple[SubworkflowSummary, ...]:
        grouped: dict[str, list[WorkflowDefinition]] = {}
        for definition in self._rows.values():
            grouped.setdefault(definition.id, []).append(definition)
        from packaging.version import Version

        summaries: list[SubworkflowSummary] = []
        for sub_id, items in grouped.items():
            items.sort(key=lambda d: Version(d.version), reverse=True)
            latest = items[0]
            summaries.append(
                SubworkflowSummary(
                    subworkflow_id=sub_id,
                    latest_version=latest.version,
                    name=latest.name,
                    description=latest.description,
                    input_count=len(latest.inputs),
                    output_count=len(latest.outputs),
                    version_count=len(items),
                ),
            )
        return tuple(summaries)[:limit]

    async def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[SubworkflowSummary, ...]:
        q = query.lower()
        summaries = await self.list_summaries()
        matched = sorted(
            (s for s in summaries if q in s.name.lower() or q in s.description.lower()),
            key=lambda s: s.subworkflow_id,
        )
        return tuple(matched[offset : offset + limit])

    async def delete(
        self,
        entity_id: tuple[str, str],
    ) -> bool:
        if entity_id in self._rows:
            del self._rows[entity_id]
            return True
        return False

    async def delete_if_unreferenced(
        self,
        subworkflow_id: str,
        version: str,
    ) -> tuple[bool, tuple[ParentReference, ...]]:
        parents = await self.find_parents(subworkflow_id, version)
        if parents:
            return False, parents
        deleted = await self.delete((subworkflow_id, version))
        return deleted, ()

    async def find_parents(
        self,
        subworkflow_id: str,
        version: str | None = None,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ParentReference, ...]:
        matching = sorted(
            (
                p
                for p in self._parents.get(subworkflow_id, [])
                if version is None or p.pinned_version == version
            ),
            key=lambda r: (
                r.parent_type,
                r.parent_id,
                r.node_id,
                r.pinned_version,
            ),
        )
        return tuple(matching[offset : offset + limit])

    def add_parent(self, subworkflow_id: str, parent: ParentReference) -> None:
        """Test helper to inject a parent reference."""
        self._parents.setdefault(subworkflow_id, []).append(parent)


@pytest.fixture
def registry() -> SubworkflowRegistry:
    return SubworkflowRegistry(FakeSubworkflowRepository())


class TestRegister:
    @pytest.mark.unit
    async def test_register_sets_registered_event(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        sub = _make_subworkflow()
        await registry.register(sub)

        fetched = await registry.get("sub-finance-close", "1.0.0")
        assert fetched.id == "sub-finance-close"

    @pytest.mark.unit
    async def test_register_rejects_non_subworkflow(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        sub = _make_subworkflow(is_subworkflow=False)
        with pytest.raises(
            SubworkflowIOError,
            match="is_subworkflow flag is False",
        ):
            await registry.register(sub)

    @pytest.mark.unit
    async def test_register_duplicate_raises(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        sub = _make_subworkflow()
        await registry.register(sub)
        with pytest.raises(DuplicateRecordError):
            await registry.register(sub)


class TestGet:
    @pytest.mark.unit
    async def test_get_missing_raises(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        with pytest.raises(SubworkflowNotFoundError) as exc_info:
            await registry.get("sub-missing", "1.0.0")
        assert exc_info.value.subworkflow_id == "sub-missing"
        assert exc_info.value.version == "1.0.0"


class TestVersionOrdering:
    @pytest.mark.unit
    async def test_list_versions_semver_descending(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        for version in ["1.0.0", "1.9.0", "1.10.0", "2.0.0"]:
            await registry.register(_make_subworkflow(version=version))

        versions = await registry.list_versions("sub-finance-close")
        assert versions == ("2.0.0", "1.10.0", "1.9.0", "1.0.0")

    @pytest.mark.unit
    async def test_latest_version(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        await registry.register(_make_subworkflow(version="1.9.0"))
        await registry.register(_make_subworkflow(version="1.10.0"))
        latest = await registry.latest_version("sub-finance-close")
        assert latest == "1.10.0"

    @pytest.mark.unit
    async def test_latest_version_missing(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        assert await registry.latest_version("sub-nope") is None


class TestDeleteProtection:
    @pytest.mark.unit
    async def test_delete_blocked_by_pinned_parent(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        sub = _make_subworkflow()
        await registry.register(sub)

        fake_repo = registry._repo
        assert isinstance(fake_repo, FakeSubworkflowRepository)
        fake_repo.add_parent(
            "sub-finance-close",
            ParentReference(
                parent_id="parent-1",
                parent_name="Parent Workflow",
                pinned_version="1.0.0",
                node_id="sub-node",
                parent_type="workflow_definition",
            ),
        )

        with pytest.raises(
            SubworkflowIOError,
            match="still referenced by 1 parent",
        ):
            await registry.delete("sub-finance-close", "1.0.0")

    @pytest.mark.unit
    async def test_delete_missing_raises(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        with pytest.raises(SubworkflowNotFoundError):
            await registry.delete("sub-missing", "1.0.0")

    @pytest.mark.unit
    async def test_delete_success_without_parents(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        sub = _make_subworkflow()
        await registry.register(sub)
        await registry.delete("sub-finance-close", "1.0.0")
        assert await registry.latest_version("sub-finance-close") is None


class TestSearch:
    @pytest.mark.unit
    async def test_search_matches_name(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        await registry.register(
            _make_subworkflow(subworkflow_id="sub-a", name="Alpha Close"),
        )
        await registry.register(
            _make_subworkflow(subworkflow_id="sub-b", name="Beta Review"),
        )
        results = await registry.search("alpha")
        assert len(results) == 1
        assert results[0].subworkflow_id == "sub-a"
