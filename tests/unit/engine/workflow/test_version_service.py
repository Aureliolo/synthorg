"""Tests for WorkflowVersionService."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import WorkflowEdgeType, WorkflowNodeType, WorkflowType
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.engine.workflow.version_service import WorkflowVersionService
from synthorg.persistence.version_repo import VersionRepository
from synthorg.versioning.models import VersionSnapshot


def _make_repo() -> AsyncMock:
    """Build a typed mock for the VersionRepository protocol.

    Spec'ing against the protocol catches accidental rename / signature
    drift between the service and its repo dependency.
    """
    return AsyncMock(spec=VersionRepository)


def _service(repo: AsyncMock | None = None) -> WorkflowVersionService:
    return WorkflowVersionService(version_repo=repo or _make_repo())


def _definition(revision: int = 1) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=NotBlankStr("wfdef-1"),
        name=NotBlankStr("Test"),
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=NotBlankStr(f"1.0.{revision}"),
        nodes=(
            WorkflowNode(
                id=NotBlankStr("start"),
                type=WorkflowNodeType.START,
                label=NotBlankStr("Start"),
            ),
            WorkflowNode(
                id=NotBlankStr("end"),
                type=WorkflowNodeType.END,
                label=NotBlankStr("End"),
            ),
        ),
        edges=(
            WorkflowEdge(
                id=NotBlankStr("e1"),
                source_node_id=NotBlankStr("start"),
                target_node_id=NotBlankStr("end"),
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
        ),
        created_by=NotBlankStr("test"),
        created_at=datetime(2026, 4, 25, tzinfo=UTC),
        updated_at=datetime(2026, 4, 25, tzinfo=UTC),
        revision=revision,
    )


def _snapshot(revision: int = 1) -> VersionSnapshot[WorkflowDefinition]:
    """Build a real VersionSnapshot wrapping a real WorkflowDefinition.

    The service does not unpack the snapshot fields; the test only
    needs equality-comparable instances so we can compare returned
    pages against the input mock data.
    """
    return VersionSnapshot[WorkflowDefinition](
        entity_id=NotBlankStr("wfdef-1"),
        version=revision,
        content_hash=NotBlankStr(f"{revision:064x}"),
        snapshot=_definition(revision),
        saved_by=NotBlankStr("test"),
        saved_at=datetime(2026, 4, 25, tzinfo=UTC),
    )


class TestListVersions:
    @pytest.mark.unit
    async def test_returns_page_and_total(self) -> None:
        repo = _make_repo()
        repo.count_versions.return_value = 7
        snap_a = _snapshot(2)
        snap_b = _snapshot(1)
        repo.list_versions.return_value = (snap_a, snap_b)
        service = _service(repo)
        page, total = await service.list_versions(
            NotBlankStr("wfdef-1"),
            offset=0,
            limit=2,
        )
        assert total == 7
        assert page == (snap_a, snap_b)
        repo.list_versions.assert_awaited_once_with(
            NotBlankStr("wfdef-1"),
            limit=2,
            offset=0,
        )

    @pytest.mark.unit
    async def test_invalid_offset_rejected(self) -> None:
        service = _service()
        with pytest.raises(ValueError, match="offset"):
            await service.list_versions(
                NotBlankStr("wfdef-1"),
                offset=-1,
                limit=10,
            )

    @pytest.mark.unit
    async def test_invalid_limit_rejected(self) -> None:
        service = _service()
        with pytest.raises(ValueError, match="limit"):
            await service.list_versions(
                NotBlankStr("wfdef-1"),
                offset=0,
                limit=0,
            )

    @pytest.mark.unit
    async def test_count_failure_propagates_through_taskgroup(self) -> None:
        """If ``count_versions`` raises, the TaskGroup propagates it.

        Regression test for the count + list parallelization: a failure
        in either branch must surface, not be swallowed by the other
        branch's success. asyncio.TaskGroup wraps failures in
        ExceptionGroup, which we assert on directly.
        """
        repo = _make_repo()
        repo.list_versions.return_value = (_snapshot(1),)
        repo.count_versions.side_effect = RuntimeError("count failed")
        service = _service(repo)
        with pytest.raises(BaseExceptionGroup) as excinfo:
            await service.list_versions(
                NotBlankStr("wfdef-1"),
                offset=0,
                limit=10,
            )
        # The group should contain exactly the RuntimeError raised by
        # ``count_versions``; other tasks' results are discarded.
        runtime_errors = [
            exc for exc in excinfo.value.exceptions if isinstance(exc, RuntimeError)
        ]
        assert len(runtime_errors) == 1
        assert "count failed" in str(runtime_errors[0])


class TestGetVersion:
    @pytest.mark.unit
    async def test_returns_snapshot(self) -> None:
        repo = _make_repo()
        snap = _snapshot(3)
        repo.get_version.return_value = snap
        service = _service(repo)
        result = await service.get_version(NotBlankStr("wfdef-1"), 3)
        assert result == snap
        repo.get_version.assert_awaited_once_with(NotBlankStr("wfdef-1"), 3)

    @pytest.mark.unit
    async def test_returns_none_when_missing(self) -> None:
        repo = _make_repo()
        repo.get_version.return_value = None
        service = _service(repo)
        result = await service.get_version(NotBlankStr("wfdef-1"), 99)
        assert result is None

    @pytest.mark.unit
    async def test_invalid_revision_rejected(self) -> None:
        service = _service()
        with pytest.raises(ValueError, match="revision"):
            await service.get_version(NotBlankStr("wfdef-1"), 0)
