"""Tests for workflow blueprint API endpoints."""

from pathlib import Path

import pytest

from synthorg.engine.workflow import blueprint_loader
from synthorg.engine.workflow.blueprint_loader import BUILTIN_BLUEPRINTS
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers


@pytest.fixture(autouse=True)
def _isolate_user_blueprints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent user-override blueprints from leaking into builtin tests."""
    monkeypatch.setattr(blueprint_loader, "_USER_BLUEPRINTS_DIR", tmp_path)


# ── GET /workflows/blueprints ────────────────────────────────────


class TestListWorkflowBlueprints:
    """Tests for the blueprint listing endpoint."""

    @pytest.mark.unit
    async def test_returns_200_with_blueprints(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/workflows/blueprints",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] is not None
        assert len(body["data"]) == len(BUILTIN_BLUEPRINTS)

    @pytest.mark.unit
    async def test_returns_blueprint_info_fields(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/workflows/blueprints",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        bp = body["data"][0]
        assert "name" in bp
        assert "display_name" in bp
        assert "description" in bp
        assert "source" in bp
        assert "tags" in bp
        assert "workflow_type" in bp
        assert "node_count" in bp
        assert "edge_count" in bp

    @pytest.mark.unit
    async def test_all_builtins_present(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/workflows/blueprints",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        names = {bp["name"] for bp in resp.json()["data"]}
        for builtin_name in BUILTIN_BLUEPRINTS:
            assert builtin_name in names


# ── POST /workflows/from-blueprint ───────────────────────────────


class TestCreateFromBlueprint:
    """Tests for the blueprint instantiation endpoint."""

    @pytest.mark.unit
    async def test_creates_workflow_from_valid_blueprint(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/workflows/from-blueprint",
            json={"blueprint_name": "feature-pipeline"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        body = resp.json()
        data = body["data"]
        assert data["id"].startswith("wfdef-")
        assert data["name"] == "Feature Pipeline"
        assert data["workflow_type"] == "sequential_pipeline"

    @pytest.mark.unit
    async def test_created_workflow_has_correct_nodes_and_edges(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/workflows/from-blueprint",
            json={"blueprint_name": "feature-pipeline"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        # Feature pipeline: start + 5 tasks + end = 7 nodes, 6 edges
        assert len(data["nodes"]) == 7
        assert len(data["edges"]) == 6

    @pytest.mark.unit
    async def test_custom_name_overrides(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/workflows/from-blueprint",
            json={
                "blueprint_name": "feature-pipeline",
                "name": "My Custom Pipeline",
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "My Custom Pipeline"

    @pytest.mark.unit
    async def test_custom_description_overrides(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/workflows/from-blueprint",
            json={
                "blueprint_name": "feature-pipeline",
                "description": "My custom description",
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["description"] == "My custom description"

    @pytest.mark.unit
    async def test_unknown_blueprint_returns_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/workflows/from-blueprint",
            json={"blueprint_name": "nonexistent"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    @pytest.mark.unit
    async def test_blank_blueprint_name_returns_400(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/workflows/from-blueprint",
            json={"blueprint_name": ""},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    @pytest.mark.unit
    async def test_created_workflow_is_editable(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """Workflow created from blueprint can be updated like any other."""
        create_resp = await async_test_client.post(
            "/api/v1/workflows/from-blueprint",
            json={"blueprint_name": "feature-pipeline"},
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        wf_id = create_resp.json()["data"]["id"]

        patch_resp = await async_test_client.patch(
            f"/api/v1/workflows/{wf_id}",
            json={"name": "Renamed Pipeline", "expected_revision": 1},
            headers=make_auth_headers("ceo"),
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["name"] == "Renamed Pipeline"

        # The retired ``expected_version`` payload key MUST be rejected
        # under the stricter request-DTO validation -- a stale client
        # still sending it would silently no-op against the optimistic-
        # concurrency guard otherwise.  ``extra="forbid"`` on the
        # update DTO produces a 4xx VALIDATION envelope; the offending
        # key text is consumed by Litestar's request-validation layer
        # before the controller runs, so we assert the rejection itself
        # via the RFC 9457 envelope rather than substring-matching the
        # generic "Validation failed" message.
        stale_resp = await async_test_client.patch(
            f"/api/v1/workflows/{wf_id}",
            json={"name": "Renamed Pipeline", "expected_version": 1},
            headers=make_auth_headers("ceo"),
        )
        assert stale_resp.status_code in (400, 422)
        body = stale_resp.json()
        assert body["success"] is False
        assert body["error_detail"]["error_category"] == "validation"

    @pytest.mark.unit
    @pytest.mark.parametrize("blueprint_name", sorted(BUILTIN_BLUEPRINTS))
    async def test_all_builtins_instantiate(
        self, async_test_client: LoopAsyncClient, blueprint_name: str
    ) -> None:
        """Every built-in blueprint can be instantiated successfully."""
        resp = await async_test_client.post(
            "/api/v1/workflows/from-blueprint",
            json={"blueprint_name": blueprint_name},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["id"].startswith("wfdef-")
        assert len(data["nodes"]) >= 3
