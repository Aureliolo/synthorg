"""Tests for the subworkflow API controller."""

from typing import Any

import pytest
from litestar.testing import TestClient

from tests.unit.api.conftest import make_auth_headers

_SUB_ID = "sub-finance-close"


def _sub_payload(
    *,
    subworkflow_id: str | None = _SUB_ID,
    version: str = "1.0.0",
    name: str = "Finance Close",
) -> dict[str, Any]:
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


def _create_subworkflow(
    test_client: TestClient[Any],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload or _sub_payload()
    resp = test_client.post(
        "/api/v1/subworkflows",
        json=body,
        headers=make_auth_headers("ceo"),
    )
    assert resp.status_code == 201, resp.text
    result: dict[str, Any] = resp.json()["data"]
    return result


@pytest.mark.unit
class TestSubworkflowCrud:
    def test_create_and_list(self, test_client: TestClient[Any]) -> None:
        _create_subworkflow(test_client)

        resp = test_client.get(
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

    def test_list_paginates_with_explicit_limit(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _create_subworkflow(
            test_client, _sub_payload(subworkflow_id="sub-a", name="Sub A")
        )
        _create_subworkflow(
            test_client, _sub_payload(subworkflow_id="sub-b", name="Sub B")
        )
        _create_subworkflow(
            test_client, _sub_payload(subworkflow_id="sub-c", name="Sub C")
        )

        first = test_client.get(
            "/api/v1/subworkflows",
            params={"limit": 2},
            headers=make_auth_headers("ceo"),
        ).json()
        assert len(first["data"]) == 2
        assert first["pagination"]["has_more"] is True
        cursor = first["pagination"]["next_cursor"]
        assert cursor is not None

        second = test_client.get(
            "/api/v1/subworkflows",
            params={"limit": 2, "cursor": cursor},
            headers=make_auth_headers("ceo"),
        ).json()
        assert len(second["data"]) == 1
        assert second["pagination"]["has_more"] is False
        first_ids = {s["subworkflow_id"] for s in first["data"]}
        second_ids = {s["subworkflow_id"] for s in second["data"]}
        assert first_ids.isdisjoint(second_ids)

    def test_list_sort_tiebreaks_on_subworkflow_id(
        self,
        test_client: TestClient[Any],
    ) -> None:
        # Two summaries sharing (name, latest_version) must paginate
        # in a stable, total order. Without the subworkflow_id
        # tie-breaker, ``registry.list_all()`` could return them in
        # different orders across requests, producing duplicates or
        # skips when clients follow ``next_cursor``. Distinct IDs
        # `sub-a` < `sub-b` lexicographically, so a correct total sort
        # places `sub-a` first regardless of insertion order.
        _create_subworkflow(
            test_client, _sub_payload(subworkflow_id="sub-b", name="shared-name")
        )
        _create_subworkflow(
            test_client, _sub_payload(subworkflow_id="sub-a", name="shared-name")
        )

        body = test_client.get(
            "/api/v1/subworkflows",
            params={"limit": 1},
            headers=make_auth_headers("ceo"),
        ).json()
        assert [s["subworkflow_id"] for s in body["data"]] == ["sub-a"]
        cursor = body["pagination"]["next_cursor"]
        assert cursor is not None

        second = test_client.get(
            "/api/v1/subworkflows",
            params={"limit": 1, "cursor": cursor},
            headers=make_auth_headers("ceo"),
        ).json()
        assert [s["subworkflow_id"] for s in second["data"]] == ["sub-b"]
        assert second["pagination"]["has_more"] is False

    def test_list_invalid_cursor_returns_400(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _create_subworkflow(test_client)
        resp = test_client.get(
            "/api/v1/subworkflows?cursor=not-a-real-cursor",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    def test_list_versions_semver_descending(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _create_subworkflow(test_client, _sub_payload(version="1.0.0"))
        _create_subworkflow(test_client, _sub_payload(version="1.9.0"))
        _create_subworkflow(test_client, _sub_payload(version="1.10.0"))

        resp = test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        versions = resp.json()["data"]
        assert versions == ["1.10.0", "1.9.0", "1.0.0"]

    def test_list_versions_pagination(
        self,
        test_client: TestClient[Any],
    ) -> None:
        # Five versions; ``limit=2&cursor=`` returns the first page;
        # ``limit=2&cursor=<next_cursor>`` returns the second.
        for v in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"):
            _create_subworkflow(test_client, _sub_payload(version=v))

        first = test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions",
            params={"limit": 2},
            headers=make_auth_headers("ceo"),
        )
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body["data"]) == 2
        assert first_body["pagination"]["has_more"] is True
        assert first_body["pagination"]["next_cursor"]

        second = test_client.get(
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

    def test_list_versions_rejects_tampered_cursor(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _create_subworkflow(test_client)
        resp = test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions",
            params={"limit": 2, "cursor": "obviously-not-signed"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    def test_get_version_missing_returns_404(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            "/api/v1/subworkflows/sub-nope/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    def test_get_version_round_trip(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _create_subworkflow(test_client)
        resp = test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == _SUB_ID
        assert data["version"] == "1.0.0"
        assert data["is_subworkflow"] is True

    def test_delete_version(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _create_subworkflow(test_client, _sub_payload(version="1.0.0"))
        _create_subworkflow(test_client, _sub_payload(version="2.0.0"))

        resp = test_client.delete(
            f"/api/v1/subworkflows/{_SUB_ID}/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200

        versions_resp = test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert versions_resp.json()["data"] == ["2.0.0"]

    def test_create_rejects_non_subworkflow_flag_bypass(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """The controller always registers with is_subworkflow=True."""
        _create_subworkflow(test_client)
        resp = test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.json()["data"]["is_subworkflow"] is True

    def test_duplicate_version_returns_409(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _create_subworkflow(test_client)
        resp = test_client.post(
            "/api/v1/subworkflows",
            json=_sub_payload(),
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409

    def test_find_parents_endpoint(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _create_subworkflow(test_client)
        resp = test_client.get(
            f"/api/v1/subworkflows/{_SUB_ID}/versions/1.0.0/parents",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        parents = resp.json()["data"]
        assert parents == []


@pytest.mark.unit
class TestSubworkflowControllerErrorEnvelope:
    """RFC 9457 envelope shape from centralised exception_handlers."""

    def test_create_invalid_definition_envelope(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        bad = _sub_payload()
        bad_nodes = list(bad["nodes"])
        bad_nodes[0] = dict(bad_nodes[0])
        bad_nodes[0]["position_x"] = "not-a-float"
        bad["nodes"] = bad_nodes

        resp = test_client.post(
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

    def test_create_duplicate_envelope(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        _create_subworkflow(test_client)
        resp = test_client.post(
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

    def test_delete_version_not_found_envelope(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        resp = test_client.delete(
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

    def test_get_version_not_found_envelope(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        resp = test_client.get(
            "/api/v1/subworkflows/sub-missing/versions/1.0.0",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.SUBWORKFLOW_NOT_FOUND
        assert detail["error_category"] == ErrorCategory.NOT_FOUND

    def test_delete_version_still_referenced_envelope(
        self,
        test_client: TestClient[Any],
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

        _create_subworkflow(test_client)

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

        resp = test_client.delete(
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
