# mypy: disable-error-code="explicit-any"
"""Unit tests for entry-point adapters."""

from typing import Any

import pytest

from synthorg.client.adapters import DirectAdapter, IntakeAdapter, ProjectAdapter
from synthorg.client.models import ClientRequest, TaskRequirement

_REQ = TaskRequirement(title="Test", description="Test description")


def _request(**overrides: Any) -> ClientRequest:
    return ClientRequest(
        client_id=overrides.pop("client_id", "c-1"),
        requirement=overrides.pop("requirement", _REQ),
        **overrides,
    )


@pytest.mark.unit
class TestDirectAdapter:
    async def test_stamps_entry_point(self) -> None:
        adapter = DirectAdapter()
        result = await adapter.route(_request())
        assert result.metadata["entry_point"] == "direct"

    async def test_preserves_request_id(self) -> None:
        req = _request()
        result = await DirectAdapter().route(req)
        assert result.request_id == req.request_id

    async def test_preserves_existing_metadata(self) -> None:
        req = _request(metadata={"custom": "value"})
        result = await DirectAdapter().route(req)
        assert result.metadata["custom"] == "value"
        assert result.metadata["entry_point"] == "direct"


@pytest.mark.unit
class TestProjectAdapter:
    async def test_stamps_project_id(self) -> None:
        adapter = ProjectAdapter(project_id="proj-1")
        result = await adapter.route(_request())
        assert result.metadata["project_id"] == "proj-1"
        assert result.metadata["entry_point"] == "project"

    async def test_preserves_existing_metadata(self) -> None:
        req = _request(metadata={"key": "val"})
        original_meta = dict(req.metadata)
        adapter = ProjectAdapter(project_id="proj-2")
        result = await adapter.route(req)
        assert result.metadata["key"] == "val"
        assert result.metadata["project_id"] == "proj-2"
        assert dict(req.metadata) == original_meta


@pytest.mark.unit
class TestIntakeAdapter:
    async def test_stamps_entry_point(self) -> None:
        adapter = IntakeAdapter()
        result = await adapter.route(_request())
        assert result.metadata["entry_point"] == "intake"

    async def test_does_not_alter_status(self) -> None:
        req = _request()
        result = await IntakeAdapter().route(req)
        assert result.status == req.status
