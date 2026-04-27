"""Conformance tests for ontology entity + drift repositories.

Issue #1457 folds the parallel ``OntologyBackend`` abstraction into
``persistence/`` with two new protocols: ``OntologyEntityRepository``
and ``OntologyDriftReportRepository``.  The prior SQLite-only tests
were removed in the consolidation; this file restores coverage and
adds a matching pass against Postgres via the shared ``backend``
fixture.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.ontology.errors import (
    OntologyDuplicateError,
    OntologyNotFoundError,
)
from synthorg.ontology.models import (
    AgentDrift,
    DriftAction,
    DriftReport,
    EntityDefinition,
    EntityField,
    EntitySource,
    EntityTier,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _entity(
    name: str = "Widget",
    *,
    tier: EntityTier = EntityTier.USER,
    definition: str = "A thing that gets shipped.",
) -> EntityDefinition:
    now = datetime.now(UTC)
    return EntityDefinition(
        name=NotBlankStr(name),
        tier=tier,
        source=EntitySource.API,
        definition=definition,
        fields=(
            EntityField(
                name=NotBlankStr("id"),
                type_hint=NotBlankStr("str"),
                description="Unique identifier",
            ),
        ),
        constraints=("id must be globally unique.",),
        disambiguation="Not a gadget, not a gizmo.",
        relationships=(),
        created_by=NotBlankStr("user_alice"),
        created_at=now,
        updated_at=now,
    )


# ── OntologyEntityRepository ────────────────────────────────────


class TestOntologyEntityRepository:
    async def test_register_and_get(self, backend: PersistenceBackend) -> None:
        entity = _entity()
        await backend.ontology_entities.register(entity)
        fetched = await backend.ontology_entities.get("Widget")
        assert fetched.name == "Widget"
        assert fetched.tier == EntityTier.USER
        assert fetched.definition == "A thing that gets shipped."

    async def test_register_duplicate_raises(self, backend: PersistenceBackend) -> None:
        await backend.ontology_entities.register(_entity("Duplicate"))
        with pytest.raises(OntologyDuplicateError):
            await backend.ontology_entities.register(_entity("Duplicate"))

    async def test_get_missing_raises(self, backend: PersistenceBackend) -> None:
        with pytest.raises(OntologyNotFoundError):
            await backend.ontology_entities.get("NeverRegistered")

    async def test_update_overwrites(self, backend: PersistenceBackend) -> None:
        entity = _entity("Updatable", definition="original")
        await backend.ontology_entities.register(entity)
        updated = entity.model_copy(
            update={
                "definition": "revised",
                "updated_at": datetime.now(UTC),
            },
        )
        await backend.ontology_entities.update(updated)
        fetched = await backend.ontology_entities.get("Updatable")
        assert fetched.definition == "revised"

    async def test_update_missing_raises(self, backend: PersistenceBackend) -> None:
        with pytest.raises(OntologyNotFoundError):
            await backend.ontology_entities.update(_entity("NoSuchEntity"))

    async def test_delete_removes_entity(self, backend: PersistenceBackend) -> None:
        await backend.ontology_entities.register(_entity("Deletable"))
        await backend.ontology_entities.delete("Deletable")
        with pytest.raises(OntologyNotFoundError):
            await backend.ontology_entities.get("Deletable")

    async def test_delete_missing_raises(self, backend: PersistenceBackend) -> None:
        with pytest.raises(OntologyNotFoundError):
            await backend.ontology_entities.delete("NeverHere")

    async def test_list_entities_filters_by_tier(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.ontology_entities.register(
            _entity("CoreEntity", tier=EntityTier.CORE),
        )
        await backend.ontology_entities.register(
            _entity("UserEntity", tier=EntityTier.USER),
        )
        core = await backend.ontology_entities.list_entities(
            tier=EntityTier.CORE,
        )
        user = await backend.ontology_entities.list_entities(
            tier=EntityTier.USER,
        )
        assert "CoreEntity" in {e.name for e in core}
        assert "UserEntity" not in {e.name for e in core}
        assert "UserEntity" in {e.name for e in user}
        assert "CoreEntity" not in {e.name for e in user}

    async def test_search_matches_name_and_definition(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.ontology_entities.register(
            _entity("Searchable", definition="findme marker text"),
        )
        await backend.ontology_entities.register(
            _entity("OtherEntity", definition="unrelated"),
        )
        by_name = await backend.ontology_entities.search("earch")
        by_def = await backend.ontology_entities.search("findme")
        assert "Searchable" in {e.name for e in by_name}
        assert "Searchable" in {e.name for e in by_def}

    async def test_list_entities_pagination(self, backend: PersistenceBackend) -> None:
        for name in ("AlphaEnt", "BetaEnt", "GammaEnt", "DeltaEnt"):
            await backend.ontology_entities.register(_entity(name))

        page = await backend.ontology_entities.list_entities(limit=2, offset=1)

        # The protocol does not guarantee a sort order; the contract
        # this assertion covers is that ``limit`` returns ``<= limit``
        # rows and ``offset`` skips at least one row.
        assert len(page) == 2
        full = await backend.ontology_entities.list_entities()
        assert len(full) >= 4
        assert page[0] != full[0]  # offset advanced past the first row

    async def test_search_pagination(self, backend: PersistenceBackend) -> None:
        for i in range(4):
            await backend.ontology_entities.register(
                _entity(
                    f"PagedEnt{i}",
                    definition=f"common-pagination-marker entry {i}",
                ),
            )

        page = await backend.ontology_entities.search(
            "pagination-marker",
            limit=2,
            offset=0,
        )

        assert len(page) == 2

    async def test_backend_name_matches_fixture(
        self, backend: PersistenceBackend, request: pytest.FixtureRequest
    ) -> None:
        # ``NotBlankStr`` wrapping is enforced by the protocol -- both
        # impls must return something non-empty that matches the
        # parametrize id.
        expected = request.node.callspec.params["backend"]
        assert backend.ontology_entities.backend_name == expected


# ── OntologyDriftReportRepository ───────────────────────────────


def _drift_report(
    entity: str = "Widget",
    *,
    divergence: float = 0.4,
    recommendation: DriftAction = DriftAction.NOTIFY,
) -> DriftReport:
    return DriftReport(
        entity_name=NotBlankStr(entity),
        divergence_score=divergence,
        divergent_agents=(
            AgentDrift(
                agent_id=NotBlankStr("agent_a"),
                divergence_score=divergence,
                details="agent A details",
            ),
        ),
        canonical_version=1,
        recommendation=recommendation,
    )


class TestOntologyDriftReportRepository:
    async def test_store_and_get_latest(self, backend: PersistenceBackend) -> None:
        await backend.ontology_drift.store_report(_drift_report("Widget"))
        rows = await backend.ontology_drift.get_latest(NotBlankStr("Widget"))
        assert len(rows) >= 1
        assert rows[0].entity_name == "Widget"

    async def test_get_latest_honours_limit(self, backend: PersistenceBackend) -> None:
        for idx in range(5):
            await backend.ontology_drift.store_report(
                _drift_report("Repeated", divergence=idx / 10),
            )
        rows = await backend.ontology_drift.get_latest(NotBlankStr("Repeated"), limit=2)
        assert len(rows) <= 2

    async def test_get_latest_missing_entity_empty(
        self, backend: PersistenceBackend
    ) -> None:
        rows = await backend.ontology_drift.get_latest(
            NotBlankStr("NoReports"),
        )
        assert rows == ()

    async def test_get_all_latest_returns_one_per_entity(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.ontology_drift.store_report(
            _drift_report("EntityA", divergence=0.2),
        )
        await backend.ontology_drift.store_report(
            _drift_report("EntityA", divergence=0.3),
        )
        await backend.ontology_drift.store_report(
            _drift_report("EntityB", divergence=0.5),
        )
        rows = await backend.ontology_drift.get_all_latest()
        by_entity = {r.entity_name: r for r in rows}
        assert "EntityA" in by_entity
        assert "EntityB" in by_entity
        # Latest score for EntityA should be the most recent (0.3)
        assert by_entity["EntityA"].divergence_score == pytest.approx(0.3)
