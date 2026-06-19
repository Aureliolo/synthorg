"""Tests for the ``GET /meta/ab-tests`` read endpoints.

The endpoints read durable :class:`AbTestRecord` rows through the
soft ``ab_test_repo`` accessor: a wired repo serves real rollouts; an
unwired repo degrades to an empty page / 404 rather than 503-ing.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.rollout.ab_models import (
    AbTestArm,
    AbTestRecord,
    AbTestStatus,
    ABTestVerdict,
)
from synthorg.meta.state import MetaStateSlice
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

_BASE = "/api/v1/meta/ab-tests"
_HEADERS = make_auth_headers("ceo")
_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _record(record_id: str = "proposal-1") -> AbTestRecord:
    return AbTestRecord(
        id=NotBlankStr(record_id),
        name=NotBlankStr(f"ab {record_id}"),
        status=AbTestStatus.COMPLETED,
        arms=(
            AbTestArm(name=NotBlankStr("control"), agent_count=5, fraction=0.5),
            AbTestArm(name=NotBlankStr("treatment"), agent_count=5, fraction=0.5),
        ),
        verdict=ABTestVerdict.TREATMENT_WINS,
        observation_hours_elapsed=24.0,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _FakeAbTestRepo:
    def __init__(self, records: tuple[AbTestRecord, ...]) -> None:
        self._records = records

    async def save(self, entity: AbTestRecord, /) -> None:
        self._records = (*self._records, entity)

    async def get(self, entity_id: NotBlankStr, /) -> AbTestRecord | None:
        return next((r for r in self._records if r.id == entity_id), None)

    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        return False

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[AbTestRecord, ...]:
        return self._records[offset : offset + limit]


@pytest.mark.unit
class TestListAbTests:
    """``GET /meta/ab-tests`` pages durable records, empty when unwired."""

    async def test_empty_page_when_repo_absent(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, ab_test_repo=None)
        try:
            resp = await async_test_client.get(_BASE, headers=_HEADERS)
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"] == []
        finally:
            app_state.swap_slice(original)

    async def test_lists_wired_records(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, ab_test_repo=_FakeAbTestRepo((_record(),)))
        try:
            resp = await async_test_client.get(_BASE, headers=_HEADERS)
            assert resp.status_code == 200
            body = resp.json()
            assert len(body["data"]) == 1
            assert body["data"][0]["id"] == "proposal-1"
            assert body["data"][0]["verdict"] == "treatment_wins"
            assert {a["name"] for a in body["data"][0]["arms"]} == {
                "control",
                "treatment",
            }
        finally:
            app_state.swap_slice(original)


@pytest.mark.unit
class TestGetAbTestDetail:
    """``GET /meta/ab-tests/{id}`` returns a record or a typed 404."""

    async def test_returns_record_when_present(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, ab_test_repo=_FakeAbTestRepo((_record(),)))
        try:
            resp = await async_test_client.get(f"{_BASE}/proposal-1", headers=_HEADERS)
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"]["id"] == "proposal-1"
            assert body["data"]["status"] == "completed"
        finally:
            app_state.swap_slice(original)

    async def test_404_when_absent(self, async_test_client: LoopAsyncClient) -> None:
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, ab_test_repo=_FakeAbTestRepo(()))
        try:
            resp = await async_test_client.get(f"{_BASE}/missing", headers=_HEADERS)
            assert resp.status_code == 404
            body = resp.json()
            assert body["success"] is False
        finally:
            app_state.swap_slice(original)
