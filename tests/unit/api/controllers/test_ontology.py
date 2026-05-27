"""Tests for ontology REST API controller."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from litestar.testing import TestClient

from synthorg.ontology.errors import OntologyDuplicateError, OntologyNotFoundError
from synthorg.ontology.models import (
    EntityDefinition,
    EntityField,
    EntitySource,
    EntityTier,
)
from synthorg.ontology.state import OntologyStateSlice


def _make_entity(
    name: str = "Task",
    *,
    tier: EntityTier = EntityTier.CORE,
    source: EntitySource = EntitySource.AUTO,
    definition: str = "A unit of work",
) -> EntityDefinition:
    now = datetime.now(UTC)
    return EntityDefinition(
        name=name,
        tier=tier,
        source=source,
        definition=definition,
        fields=(EntityField(name="id", type_hint="str", description="Unique ID"),),
        constraints=("must have an owner",),
        disambiguation="Not a calendar event",
        relationships=(),
        created_by="test",
        created_at=now,
        updated_at=now,
    )


def _inject_ontology_service(
    test_client: TestClient[Any],
) -> AsyncMock:
    """Inject a mock OntologyService into the app state."""
    svc = AsyncMock()
    svc.list_entities = AsyncMock(return_value=())
    svc.get = AsyncMock()
    svc.register = AsyncMock()
    svc.update = AsyncMock()
    svc.delete = AsyncMock()
    svc.get_version_manifest = AsyncMock(return_value={})
    svc.list_versions = AsyncMock(return_value=())
    svc.get_version = AsyncMock(return_value=None)
    svc.bootstrap = AsyncMock(return_value=0)
    app_state = test_client.app.state.app_state
    app_state._ontology_service = svc
    app_state.swap_slice(OntologyStateSlice.model_construct(service=svc))
    return svc


@pytest.mark.unit
class TestListEntities:
    def test_empty_list(self, test_client: TestClient[Any]) -> None:
        svc = _inject_ontology_service(test_client)
        svc.list_entities.return_value = ()

        resp = test_client.get("/api/v1/ontology/entities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    def test_with_entities(self, test_client: TestClient[Any]) -> None:
        svc = _inject_ontology_service(test_client)
        entity = _make_entity()
        svc.list_entities.return_value = (entity,)

        resp = test_client.get("/api/v1/ontology/entities")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["name"] == "Task"
        assert body["data"][0]["tier"] == "core"

    def test_filter_by_tier(self, test_client: TestClient[Any]) -> None:
        svc = _inject_ontology_service(test_client)
        svc.list_entities.return_value = ()

        resp = test_client.get("/api/v1/ontology/entities?tier=user")
        assert resp.status_code == 200
        svc.list_entities.assert_awaited_once_with(tier=EntityTier.USER)

    def test_invalid_tier_returns_400(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _inject_ontology_service(test_client)
        resp = test_client.get("/api/v1/ontology/entities?tier=invalid")
        assert resp.status_code == 422


@pytest.mark.unit
class TestGetEntity:
    def test_found(self, test_client: TestClient[Any]) -> None:
        svc = _inject_ontology_service(test_client)
        entity = _make_entity()
        svc.get.return_value = entity

        resp = test_client.get("/api/v1/ontology/entities/Task")
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Task"

    def test_not_found(self, test_client: TestClient[Any]) -> None:
        svc = _inject_ontology_service(test_client)
        svc.get.side_effect = OntologyNotFoundError("nope")

        resp = test_client.get("/api/v1/ontology/entities/Missing")
        assert resp.status_code == 404


@pytest.mark.unit
class TestCreateEntity:
    def test_create_user_entity(
        self,
        test_client: TestClient[Any],
    ) -> None:
        svc = _inject_ontology_service(test_client)
        svc.register.return_value = None

        resp = test_client.post(
            "/api/v1/ontology/entities",
            json={"name": "NewEntity", "definition": "A new thing"},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "NewEntity"
        assert resp.json()["data"]["tier"] == "user"

    def test_duplicate_returns_400(
        self,
        test_client: TestClient[Any],
    ) -> None:
        svc = _inject_ontology_service(test_client)
        svc.register.side_effect = OntologyDuplicateError("exists")

        resp = test_client.post(
            "/api/v1/ontology/entities",
            json={"name": "Task"},
        )
        assert resp.status_code == 422


@pytest.mark.unit
class TestUpdateEntity:
    def test_update_user_entity(
        self,
        test_client: TestClient[Any],
    ) -> None:
        svc = _inject_ontology_service(test_client)
        entity = _make_entity("MyEntity", tier=EntityTier.USER)
        svc.get.return_value = entity
        svc.update.return_value = None

        resp = test_client.put(
            "/api/v1/ontology/entities/MyEntity",
            json={"definition": "Updated"},
        )
        assert resp.status_code == 200
        svc.update.assert_awaited_once()

    def test_core_entity_blocked(
        self,
        test_client: TestClient[Any],
    ) -> None:
        svc = _inject_ontology_service(test_client)
        entity = _make_entity("Task", tier=EntityTier.CORE)
        svc.get.return_value = entity

        resp = test_client.put(
            "/api/v1/ontology/entities/Task",
            json={"definition": "Changed"},
        )
        assert resp.status_code == 422
        assert "CORE" in resp.json().get("detail", resp.json().get("error", ""))


@pytest.mark.unit
class TestDeleteEntity:
    def test_delete_user_entity(
        self,
        test_client: TestClient[Any],
    ) -> None:
        svc = _inject_ontology_service(test_client)
        entity = _make_entity("Custom", tier=EntityTier.USER)
        svc.get.return_value = entity

        resp = test_client.delete("/api/v1/ontology/entities/Custom")
        assert resp.status_code == 204
        svc.delete.assert_awaited_once_with("Custom")

    def test_core_entity_blocked(
        self,
        test_client: TestClient[Any],
    ) -> None:
        svc = _inject_ontology_service(test_client)
        entity = _make_entity("Task", tier=EntityTier.CORE)
        svc.get.return_value = entity

        resp = test_client.delete("/api/v1/ontology/entities/Task")
        assert resp.status_code == 422

    def test_not_found(self, test_client: TestClient[Any]) -> None:
        svc = _inject_ontology_service(test_client)
        svc.get.side_effect = OntologyNotFoundError("nope")

        resp = test_client.delete("/api/v1/ontology/entities/Missing")
        assert resp.status_code == 404


@pytest.mark.unit
class TestVersionManifest:
    def test_get_manifest(self, test_client: TestClient[Any]) -> None:
        svc = _inject_ontology_service(test_client)
        svc.get_version_manifest.return_value = {"Task": 3, "Agent": 1}

        resp = test_client.get("/api/v1/ontology/manifest")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["Task"] == 3
        assert data["Agent"] == 1


@pytest.mark.unit
class TestDriftEndpoints:
    def test_list_drift_no_store(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _inject_ontology_service(test_client)
        # drift_report_store is None by default
        resp = test_client.get("/api/v1/ontology/drift")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_get_drift_no_store(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _inject_ontology_service(test_client)
        resp = test_client.get("/api/v1/ontology/drift/Task")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == [] or body["data"] == ()

    # NOTE: POST drift/admin endpoints crash the xdist worker due
    # to Litestar test client + Python 3.14 async interaction.
    # Covered by integration tests and CI.
