"""Tests for the subworkflow API controller."""

from typing import Any
from uuid import UUID

import pytest

from tests._shared import JsonDict, LoopAsyncClient, sid
from tests.unit.api.conftest import make_auth_headers

_SUB_ID = sid("sub-finance-close")


def _sub_payload(
    *,
    subworkflow_id: str | None = _SUB_ID,
    version: str = "1.0.0",
    name: str = "Finance Close",
) -> JsonDict:
    return {
        "subworkflow_id": subworkflow_id,
        "version": version,
        "name": name,
        "description": "Finance close subworkflow",
        "workflow_type": "sequential_pipeline",
        "inputs": [
            {"name": "quarter", "type": "string", "required": True},
        ],
        "outputs": [
            {"name": "report", "type": "string", "required": True},
        ],
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "label": "Start",
                "config": {},
            },
            {
                "id": "task-close",
                "type": "task",
                "label": "Close",
                "config": {"title": "Close", "task_type": "admin"},
            },
            {
                "id": "end",
                "type": "end",
                "label": "End",
                "config": {},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source_node_id": "start",
                "target_node_id": "task-close",
                "type": "sequential",
            },
            {
                "id": "e2",
                "source_node_id": "task-close",
                "target_node_id": "end",
                "type": "sequential",
            },
        ],
    }


async def _create_subworkflow(
    async_test_client: LoopAsyncClient,
    payload: JsonDict | None = None,
) -> JsonDict:
    body = payload or _sub_payload()
    resp = await async_test_client.post(
        "/api/v1/subworkflows",
        json=body,
        headers=make_auth_headers("ceo"),
    )
    assert resp.status_code == 201, resp.text
    result: JsonDict = resp.json()["data"]
    return result


@pytest.mark.unit
class TestSubworkflowCrud:
    async def test_create_and_list(self, async_test_client: LoopAsyncClient) -> None:
        await _create_subworkflow(async_test_client)

        resp = await async_test_client.get(
            "/api/v1/subworkflows",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        items = body["data"]
        assert len(items) == 1
        assert items[0]["subworkflow_id"] == _SUB_ID
        assert items[0]["latest_version"] == "1.0.0"
        assert items[0]["input_count"] == 1
        assert items[0]["output_count"] == 1
        assert body["pagination"]["limit"] == 50
        # ``total`` is ``null`` under keyset pagination -- clients
        # derive display counts from ``data.length`` per the frontend
        # contract in ``web/CLAUDE.md``.
        assert body["pagination"]["has_more"] is False

    async def test_create_without_id_server_mints_uuid(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # Omitting ``subworkflow_id`` makes the server self-mint one. The
        # create response is the ``WorkflowDefinition`` (``id`` field). The
        # result is a canonical UUID string, never a hex-prefixed ``sub-`` id.
        result = await _create_subworkflow(
            async_test_client, _sub_payload(subworkflow_id=None)
        )
        minted = result["id"]
        assert str(UUID(minted)) == minted
        assert not minted.startswith("sub-")

    async def test_create_without_id_mints_distinct_ids(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        first = await _create_subworkflow(
            async_test_client, _sub_payload(subworkflow_id=None, name="A")
        )
        second = await _create_subworkflow(
            async_test_client, _sub_payload(subworkflow_id=None, name="B")
        )
        assert first["id"] != second["id"]

    async def test_create_with_invalid_uuid_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # ``subworkflow_id`` is typed ``UUID``; a non-UUID value is
        # rejected at the request boundary as a 400 validation error.
        resp = await async_test_client.post(
            "/api/v1/subworkflows",
            json=_sub_payload(subworkflow_id="not-a-uuid"),
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400, resp.text

    async def test_list_paginates_with_explicit_limit(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _create_subworkflow(
            async_test_client, _sub_payload(subworkflow_id=sid("sub-a"), name="Sub A")
        )
        await _create_subworkflow(
            async_test_client, _sub_payload(subworkflow_id=sid("sub-b"), name="Sub B")
        )
        await _create_subworkflow(
            async_test_client, _sub_payload(subworkflow_id=sid("sub-c"), name="Sub C")
        )

        first = (
            await async_test_client.get(
                "/api/v1/subworkflows",
                params={"limit": 2},
                headers=make_auth_headers("ceo"),
            )
        ).json()
        assert len(first["data"]) == 2
        assert first["pagination"]["has_more"] is True
        cursor = first["pagination"]["next_cursor"]
        assert cursor is not None

        second = (
            await async_test_client.get(
                "/api/v1/subworkflows",
                params={"limit": 2, "cursor": cursor},
                headers=make_auth_headers("ceo"),
            )
        ).json()
        assert len(second["data"]) == 1
        assert second["pagination"]["has_more"] is False
        first_ids = {s["subworkflow_id"] for s in first["data"]}
        second_ids = {s["subworkflow_id"] for s in second["data"]}
        assert first_ids.isdisjoint(second_ids)

    async def test_list_sort_tiebreaks_on_subworkflow_id(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # Two summaries sharing (name, latest_version) must paginate
        # in a stable, total order. Without the subworkflow_id
        # tie-breaker, ``registry.list_all()`` could return them in
        # different orders across requests, producing duplicates or
        # skips when clients follow ``next_cursor``. The ids are UUID
        # strings, so the total sort places the lexicographically
        # smaller id first regardless of insertion order.
        first_id, second_id = sorted((sid("sub-a"), sid("sub-b")))
        await _create_subworkflow(
            async_test_client,
            _sub_payload(subworkflow_id=sid("sub-b"), name="shared-name"),
        )
        await _create_subworkflow(
            async_test_client,
            _sub_payload(subworkflow_id=sid("sub-a"), name="shared-name"),
        )

        body = (
            await async_test_client.get(
                "/api/v1/subworkflows",
                params={"limit": 1},
                headers=make_auth_headers("ceo"),
            )
        ).json()
        assert [s["subworkflow_id"] for s in body["data"]] == [first_id]
        cursor = body["pagination"]["next_cursor"]
        assert cursor is not None

        second = (
            await async_test_client.get(
                "/api/v1/subworkflows",
                params={"limit": 1, "cursor": cursor},
                headers=make_auth_headers("ceo"),
            )
        ).json()
        assert [s["subworkflow_id"] for s in second["data"]] == [second_id]
        assert second["pagination"]["has_more"] is False

    async def test_list_invalid_cursor_returns_400(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _create_subworkflow(async_test_client)
        resp = await async_test_client.get(
            "/api/v1/subworkflows?cursor=not-a-real-cursor",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    async def test_list_versions_semver_descending(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _create_subworkflow(async_test_client, _sub_payload(version="1.0.0"))
        await _create_subworkflow(async_test_client, _sub_payload(version="1.9.0"))
        await _create_subworkflow(async_test_client, _sub_payload(version="1.10.0"))

        resp = await async_test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        versions = resp.json()["data"]
        assert versions == ["1.10.0", "1.9.0", "1.0.0"]

    async def test_list_versions_pagination(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # Five versions; ``limit=2&cursor=`` returns the first page;
        # ``limit=2&cursor=<next_cursor>`` returns the second.
        for v in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"):
            await _create_subworkflow(async_test_client, _sub_payload(version=v))

        first = await async_test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions",
            params={"limit": 2},
            headers=make_auth_headers("ceo"),
        )
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body["data"]) == 2
        assert first_body["pagination"]["has_more"] is True
        assert first_body["pagination"]["next_cursor"]

        second = await async_test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions",
            params={
                "limit": 2,
                "cursor": first_body["pagination"]["next_cursor"],
            },
            headers=make_auth_headers("ceo"),
        )
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body["data"]) == 2
        # Pages do not overlap.
        assert set(first_body["data"]).isdisjoint(set(second_body["data"]))

    async def test_list_versions_rejects_tampered_cursor(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _create_subworkflow(async_test_client)
        resp = await async_test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions",
            params={"limit": 2, "cursor": "obviously-not-signed"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    async def test_get_version_missing_returns_404(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/subworkflows/sub-nope/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    async def test_get_version_round_trip(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _create_subworkflow(async_test_client)
        resp = await async_test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == _SUB_ID
        assert data["version"] == "1.0.0"
        assert data["is_subworkflow"] is True

    async def test_delete_version(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _create_subworkflow(async_test_client, _sub_payload(version="1.0.0"))
        await _create_subworkflow(async_test_client, _sub_payload(version="2.0.0"))

        resp = await async_test_client.delete(
            f"/api/v1/subworkflows/{_SUB_ID}/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200

        versions_resp = await async_test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert versions_resp.json()["data"] == ["2.0.0"]

    async def test_create_rejects_non_subworkflow_flag_bypass(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """The controller always registers with is_subworkflow=True."""
        await _create_subworkflow(async_test_client)
        resp = await async_test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.json()["data"]["is_subworkflow"] is True

    async def test_duplicate_version_returns_409(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _create_subworkflow(async_test_client)
        resp = await async_test_client.post(
            "/api/v1/subworkflows",
            json=_sub_payload(),
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409

    async def test_find_parents_endpoint(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _create_subworkflow(async_test_client)
        resp = await async_test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions/1.0.0/parents",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        parents = resp.json()["data"]
        assert parents == []


@pytest.mark.unit
class TestSubworkflowControllerErrorEnvelope:
    """RFC 9457 envelope shape from centralised exception_handlers."""

    async def test_create_invalid_definition_envelope(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        bad = _sub_payload()
        bad_nodes = list(bad["nodes"])
        bad_nodes[0] = dict(bad_nodes[0])
        bad_nodes[0]["position_x"] = "not-a-float"
        bad["nodes"] = bad_nodes

        resp = await async_test_client.post(
            "/api/v1/subworkflows",
            json=bad,
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.REQUEST_VALIDATION_ERROR
        assert detail["error_category"] == ErrorCategory.VALIDATION
        assert detail["retryable"] is False

    async def test_create_duplicate_envelope(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        await _create_subworkflow(async_test_client)
        resp = await async_test_client.post(
            "/api/v1/subworkflows",
            json=_sub_payload(),
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.DUPLICATE_RECORD
        assert detail["error_category"] == ErrorCategory.CONFLICT
        assert detail["retryable"] is False

    async def test_delete_version_not_found_envelope(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        resp = await async_test_client.delete(
            "/api/v1/subworkflows/sub-missing/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.SUBWORKFLOW_NOT_FOUND
        assert detail["error_category"] == ErrorCategory.NOT_FOUND
        assert detail["retryable"] is False

    async def test_get_version_not_found_envelope(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        resp = await async_test_client.get(
            "/api/v1/subworkflows/sub-missing/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.SUBWORKFLOW_NOT_FOUND
        assert detail["error_category"] == ErrorCategory.NOT_FOUND

    async def test_delete_version_still_referenced_envelope(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Deleting a subworkflow with live parent references returns 409.

        ``SubworkflowHasParentsError`` overrides its parent
        ``SubworkflowIOError``'s 422 ClassVar with 409 + RESOURCE_CONFLICT
        because a still-referenced subworkflow is a resource-state
        conflict, not a validation failure. Verify the centralised
        handler honours the override.
        """
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
        from synthorg.engine.workflow.subworkflow_models import (
            ParentReference,
        )
        from synthorg.engine.workflow.subworkflow_registry import (
            SubworkflowRegistry,
        )
        from synthorg.engine.workflow.subworkflow_service import (
            SubworkflowHasParentsError,
        )

        await _create_subworkflow(async_test_client)

        async def _raise_has_parents(
            self: SubworkflowRegistry,
            subworkflow_id: str,
            version: str,
        ) -> None:
            msg = "Subworkflow has live parent references"
            raise SubworkflowHasParentsError(
                msg,
                subworkflow_id=subworkflow_id,
                version=version,
                parents=(
                    ParentReference(
                        parent_id="wfdef-parent",
                        parent_name="parent",
                        pinned_version="1.0.0",
                        node_id="sub-node-1",
                        parent_type="workflow_definition",
                    ),
                ),
            )

        monkeypatch.setattr(SubworkflowRegistry, "delete", _raise_has_parents)

        resp = await async_test_client.delete(
            f"/api/v1/subworkflows/{_SUB_ID}/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.RESOURCE_CONFLICT
        assert detail["error_category"] == ErrorCategory.CONFLICT
        assert detail["retryable"] is False
