"""Tests for agent identity version API endpoints."""

from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from litestar.testing import TestClient

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import SeniorityLevel
from synthorg.hr.registry import AgentRegistryService
from tests.unit.api.conftest import make_auth_headers
from tests.unit.api.fakes_backend import FakePersistenceBackend


def _make_identity(name: str = "agent-ver") -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role="developer",
        department="engineering",
        level=SeniorityLevel.MID,
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


async def _seed_versions(
    registry: AgentRegistryService,
    *,
    updates: int = 0,
) -> AgentIdentity:
    """Register an agent and issue ``updates`` charter updates.

    Each ``update_identity`` bumps ``level`` so that the content hash
    actually changes (no-op snapshots are suppressed by the service).
    """
    identity = _make_identity()
    await registry.register(identity)
    levels = (
        SeniorityLevel.SENIOR,
        SeniorityLevel.LEAD,
        SeniorityLevel.PRINCIPAL,
    )
    # Cycle through the level menu so callers can request more updates
    # than the tuple has entries without tripping an IndexError.
    for i in range(updates):
        await registry.update_identity(
            str(identity.id),
            level=levels[i % len(levels)],
        )
    return identity


class TestListVersions:
    """``GET /agents/{agent_id}/versions``."""

    @pytest.mark.unit
    async def test_single_version_after_register(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
    ) -> None:
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry)
        resp = test_client.get(
            f"/api/v1/agents/{identity.id}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["version"] == 1
        assert body["data"][0]["entity_id"] == str(identity.id)
        assert body["pagination"]["total"] == 1

    @pytest.mark.unit
    async def test_multiple_versions_after_updates(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
    ) -> None:
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry, updates=2)
        resp = test_client.get(
            f"/api/v1/agents/{identity.id}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        versions = resp.json()["data"]
        assert {v["version"] for v in versions} == {1, 2, 3}

    @pytest.mark.unit
    async def test_empty_for_unknown_agent(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            f"/api/v1/agents/{uuid4()}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0
        assert body["pagination"]["offset"] == 0
        assert body["pagination"]["limit"] == 20


class TestGetVersion:
    """``GET /agents/{agent_id}/versions/{version_num}``."""

    @pytest.mark.unit
    async def test_returns_snapshot(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
    ) -> None:
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry)
        resp = test_client.get(
            f"/api/v1/agents/{identity.id}/versions/1",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == 1
        assert data["snapshot"]["name"] == identity.name

    @pytest.mark.unit
    async def test_missing_version_returns_404(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
    ) -> None:
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry)
        resp = test_client.get(
            f"/api/v1/agents/{identity.id}/versions/42",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"].lower()


class TestDiff:
    """``GET /agents/{agent_id}/versions/diff``."""

    @pytest.mark.unit
    async def test_computes_level_diff(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
    ) -> None:
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry, updates=1)
        resp = test_client.get(
            f"/api/v1/agents/{identity.id}/versions/diff",
            params={"from_version": 1, "to_version": 2},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        diff = resp.json()["data"]
        assert diff["from_version"] == 1
        assert diff["to_version"] == 2
        paths = {c["field_path"] for c in diff["field_changes"]}
        assert "level" in paths

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("from_version", "to_version", "expected_status"),
        [
            pytest.param(1, 1, 400, id="same_versions_rejected"),
            pytest.param(2, 1, 400, id="reversed_versions_rejected"),
            pytest.param(99, 100, 404, id="missing_from_version_returns_404"),
            pytest.param(1, 99, 404, id="missing_to_version_returns_404"),
        ],
    )
    async def test_diff_validation(  # noqa: PLR0913
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
        from_version: int,
        to_version: int,
        expected_status: int,
    ) -> None:
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry, updates=1)
        resp = test_client.get(
            f"/api/v1/agents/{identity.id}/versions/diff",
            params={"from_version": from_version, "to_version": to_version},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == expected_status


class TestRollback:
    """``POST /agents/{agent_id}/versions/rollback``."""

    @pytest.mark.unit
    async def test_rolls_back_level(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
    ) -> None:
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry, updates=2)
        resp = test_client.post(
            f"/api/v1/agents/{identity.id}/versions/rollback",
            json={"target_version": 1},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["level"] == "mid"
        current = await agent_registry.get(str(identity.id))
        assert current is not None
        assert current.level is SeniorityLevel.MID

    @pytest.mark.unit
    async def test_rollback_creates_new_snapshot(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
    ) -> None:
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry, updates=1)
        test_client.post(
            f"/api/v1/agents/{identity.id}/versions/rollback",
            json={"target_version": 1},
            headers=make_auth_headers("ceo"),
        )
        resp = test_client.get(
            f"/api/v1/agents/{identity.id}/versions",
            headers=make_auth_headers("ceo"),
        )
        versions = resp.json()["data"]
        assert {v["version"] for v in versions} == {1, 2, 3}

    @pytest.mark.unit
    async def test_rollback_unknown_agent_returns_404(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
    ) -> None:
        """Covers the ``evolve_identity`` ``AgentNotFoundError`` -> 404 path.

        Seeds a version (so the repository lookup in the handler succeeds)
        and then clears the registry so ``evolve_identity`` can't find the
        agent.  Without the seed-then-clear sequence, the 404 would come
        from the earlier ``get_version`` branch instead.
        """
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry)
        await agent_registry.clear()
        resp = test_client.post(
            f"/api/v1/agents/{identity.id}/versions/rollback",
            json={"target_version": 1},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        assert "agent not found" in resp.json()["error"].lower()

    @pytest.mark.unit
    async def test_rollback_missing_target_returns_404(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
    ) -> None:
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry)
        resp = test_client.post(
            f"/api/v1/agents/{identity.id}/versions/rollback",
            json={"target_version": 99},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    @pytest.mark.unit
    async def test_rollback_with_reason_records_audit_trail(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Optional ``reason`` is forwarded into the evolution rationale.

        Spies on ``evolve_identity`` to assert the reason actually reaches
        the audit trail -- not just that the endpoint returned 200.
        """
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry, updates=1)

        captured: dict[str, str] = {}
        original = agent_registry.evolve_identity

        async def _capture(
            agent_id: str,
            snapshot: AgentIdentity,
            *,
            evolution_rationale: str,
        ) -> AgentIdentity:
            captured["rationale"] = evolution_rationale
            return await original(
                agent_id,
                snapshot,
                evolution_rationale=evolution_rationale,
            )

        monkeypatch.setattr(agent_registry, "evolve_identity", _capture)
        resp = test_client.post(
            f"/api/v1/agents/{identity.id}/versions/rollback",
            json={"target_version": 1, "reason": "undo accidental promotion"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert "undo accidental promotion" in captured["rationale"]
        assert "rollback to v1" in captured["rationale"].lower()

    @pytest.mark.unit
    async def test_rollback_evolve_value_error_returns_400(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``evolve_identity`` raising ``ValueError`` maps to a clean 400."""
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        identity = await _seed_versions(agent_registry)

        msg = "immutable field mismatch"

        async def _raise_value_error(*_args: Any, **_kwargs: Any) -> None:
            raise ValueError(msg)

        monkeypatch.setattr(agent_registry, "evolve_identity", _raise_value_error)
        resp = test_client.post(
            f"/api/v1/agents/{identity.id}/versions/rollback",
            json={"target_version": 1},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "cannot rollback" in body["error"].lower()
        assert "immutable field mismatch" in body["error"].lower()


async def _forge_cross_wired_snapshot(
    fake_persistence: FakePersistenceBackend,
    source_agent_id: str,
    target_agent_id: str,
    version: int,
    content_hash: str,
) -> None:
    """Write a ``VersionSnapshot`` whose inner payload and outer ``entity_id`` disagree.

    Simulates a corrupted row where the repository thinks the snapshot
    belongs to ``target_agent_id`` but the embedded ``AgentIdentity``
    carries ``source_agent_id``.  Used by the parametrized cross-entity
    ownership tests.
    """
    from synthorg.versioning import VersionSnapshot

    latest = await fake_persistence.identity_versions.get_latest_version(
        source_agent_id,
    )
    assert latest is not None
    forged = VersionSnapshot(
        entity_id=target_agent_id,
        version=version,
        content_hash=content_hash,
        snapshot=latest.snapshot,
        saved_by="test-forger",
        saved_at=latest.saved_at,
    )
    await fake_persistence.identity_versions.save_version(forged)


class TestReadEndpointsOwnership:
    """All endpoints reject/drop cross-entity snapshots consistently."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("method", "path_suffix", "json_body", "params"),
        [
            pytest.param("get", "/versions/42", None, None, id="get_version"),
            pytest.param(
                "get",
                "/versions/diff",
                None,
                {"from_version": 1, "to_version": 42},
                id="diff",
            ),
            pytest.param(
                "post",
                "/versions/rollback",
                {"target_version": 42},
                None,
                id="rollback",
            ),
        ],
    )
    async def test_cross_entity_snapshot_rejected(  # noqa: PLR0913
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
        method: str,
        path_suffix: str,
        json_body: dict[str, Any] | None,
        params: dict[str, int] | None,
    ) -> None:
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        alice = await _seed_versions(agent_registry, updates=1)
        bob = await _seed_versions(agent_registry)
        await _forge_cross_wired_snapshot(
            fake_persistence,
            source_agent_id=str(alice.id),
            target_agent_id=str(bob.id),
            version=42,
            content_hash="e" * 64,
        )
        url = f"/api/v1/agents/{bob.id}{path_suffix}"
        headers = make_auth_headers("ceo")
        resp = (
            test_client.get(url, params=params, headers=headers)
            if method == "get"
            else test_client.post(url, json=json_body, headers=headers)
        )
        assert resp.status_code == 400
        assert "different agent" in resp.json()["error"].lower()

    @pytest.mark.unit
    async def test_list_versions_drops_cross_entity_rows(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        agent_registry: AgentRegistryService,
    ) -> None:
        """``list_versions`` filters forged rows and adjusts ``total`` accordingly."""
        fake_persistence.identity_versions.clear()
        await agent_registry.clear()
        alice = await _seed_versions(agent_registry)
        bob = await _seed_versions(agent_registry)
        await _forge_cross_wired_snapshot(
            fake_persistence,
            source_agent_id=str(alice.id),
            target_agent_id=str(bob.id),
            version=42,
            content_hash="d" * 64,
        )
        resp = test_client.get(
            f"/api/v1/agents/{bob.id}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        # Forged row must be silently dropped from the response and
        # ``pagination.total`` must reflect only the surviving rows so
        # clients paginating by the reported total stay in sync.  Also
        # assert the legitimate row survives so over-filtering regressions
        # fail this test instead of passing silently with an empty list.
        body = resp.json()
        versions = body["data"]
        assert len(versions) == 1
        assert versions[0]["entity_id"] == str(bob.id)
        assert versions[0]["version"] == 1
        assert all(v["version"] != 42 for v in versions)
        assert body["pagination"]["total"] == len(versions)


class TestAuthGuards:
    """Guards on version endpoints reject unauthorized roles and missing auth."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("method", "path_suffix", "body"),
        [
            pytest.param("get", "/versions", None, id="list_versions"),
            pytest.param("get", "/versions/1", None, id="get_version"),
            pytest.param(
                "get",
                "/versions/diff?from_version=1&to_version=2",
                None,
                id="get_diff",
            ),
            pytest.param(
                "post",
                "/versions/rollback",
                {"target_version": 1},
                id="rollback",
            ),
        ],
    )
    def test_invalid_auth_returns_401(
        self,
        test_client: TestClient[Any],
        method: str,
        path_suffix: str,
        body: dict[str, Any] | None,
    ) -> None:
        url = f"/api/v1/agents/{uuid4()}{path_suffix}"
        headers = {"Authorization": "Bearer invalid-token"}
        resp = (
            test_client.get(url, headers=headers)
            if method == "get"
            else test_client.post(url, json=body, headers=headers)
        )
        assert resp.status_code == 401

    @pytest.mark.unit
    @pytest.mark.parametrize("role", ["observer", "board_member"])
    def test_rollback_write_guard_rejects_read_only_roles(
        self,
        test_client: TestClient[Any],
        role: str,
    ) -> None:
        resp = test_client.post(
            f"/api/v1/agents/{uuid4()}/versions/rollback",
            json={"target_version": 1},
            headers=make_auth_headers(role),
        )
        assert resp.status_code == 403

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "role",
        ["ceo", "manager", "pair_programmer", "observer", "board_member"],
    )
    def test_list_read_guard_accepts_all_human_roles(
        self,
        test_client: TestClient[Any],
        role: str,
    ) -> None:
        resp = test_client.get(
            f"/api/v1/agents/{uuid4()}/versions",
            headers=make_auth_headers(role),
        )
        # Unknown agent still returns 200 with empty list -- the important
        # thing is the guard does not 401/403 the request.
        assert resp.status_code == 200
