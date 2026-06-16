"""Conformance tests for ontology entity + drift repositories.

The ``OntologyEntityRepository`` and ``OntologyDriftReportRepository``
protocols both ship under ``persistence/``; this file exercises each
against SQLite and Postgres via the shared ``backend`` fixture so the
implementations stay in lock-step.
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
        assert fetched is not None
        assert fetched.name == "Widget"
        assert fetched.tier == EntityTier.USER
        assert fetched.definition == "A thing that gets shipped."

    async def test_register_duplicate_raises(self, backend: PersistenceBackend) -> None:
        await backend.ontology_entities.register(_entity("Duplicate"))
        with pytest.raises(OntologyDuplicateError):
            await backend.ontology_entities.register(_entity("Duplicate"))

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        fetched = await backend.ontology_entities.get("NeverRegistered")
        assert fetched is None

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
        assert fetched is not None
        assert fetched.definition == "revised"

    async def test_save_is_idempotent_upsert(self, backend: PersistenceBackend) -> None:
        # save() must upsert: a first save inserts, a repeated save
        # for the same name updates rather than raising
        # OntologyDuplicateError (the insert-only register() path).
        entity = _entity("Upsertable", definition="v1")
        await backend.ontology_entities.save(entity)
        await backend.ontology_entities.save(
            entity.model_copy(
                update={
                    "definition": "v2",
                    "updated_at": datetime.now(UTC),
                },
            ),
        )
        fetched = await backend.ontology_entities.get("Upsertable")
        assert fetched is not None
        assert fetched.definition == "v2"

    async def test_update_missing_raises(self, backend: PersistenceBackend) -> None:
        with pytest.raises(OntologyNotFoundError):
            await backend.ontology_entities.update(_entity("NoSuchEntity"))

    async def test_delete_removes_entity(self, backend: PersistenceBackend) -> None:
        await backend.ontology_entities.register(_entity("Deletable"))
        deleted = await backend.ontology_entities.delete("Deletable")
        assert deleted is True
        fetched = await backend.ontology_entities.get("Deletable")
        assert fetched is None

    async def test_delete_missing_returns_false(
        self, backend: PersistenceBackend
    ) -> None:
        deleted = await backend.ontology_entities.delete("NeverHere")
        assert deleted is False

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
        by_name = await backend.ontology_entities.search("rch")
        by_def = await backend.ontology_entities.search("findme")
        assert "Searchable" in {e.name for e in by_name}
        assert "Searchable" in {e.name for e in by_def}

    async def test_list_entities_pagination(self, backend: PersistenceBackend) -> None:
        for name in ("AlphaEnt", "BetaEnt", "GammaEnt", "DeltaEnt"):
            await backend.ontology_entities.register(_entity(name))

        # Anchor pagination assertions against the deterministic
        # ORDER BY name ASC contract on both backends so a regression
        # that breaks ordering surfaces here.
        full = await backend.ontology_entities.list_entities()
        full_names = [e.name for e in full]
        assert {"AlphaEnt", "BetaEnt", "DeltaEnt", "GammaEnt"} <= set(full_names)

        page = await backend.ontology_entities.list_entities(limit=2, offset=1)
        assert [e.name for e in page] == full_names[1:3]

    async def test_search_pagination(self, backend: PersistenceBackend) -> None:
        for i in range(4):
            await backend.ontology_entities.register(
                _entity(
                    f"PagedEnt{i}",
                    definition=f"common-pagination-marker entry {i}",
                ),
            )

        # Compare two adjacent windows so the assertion proves offset
        # actually advances rather than just bounding the row count.
        first_page = await backend.ontology_entities.search(
            "pagination-marker",
            limit=2,
            offset=0,
        )
        second_page = await backend.ontology_entities.search(
            "pagination-marker",
            limit=2,
            offset=2,
        )
        assert len(first_page) == 2
        assert len(second_page) == 2
        first_names = {e.name for e in first_page}
        second_names = {e.name for e in second_page}
        assert first_names.isdisjoint(second_names)

    async def test_save_upserts_like_register(
        self, backend: PersistenceBackend
    ) -> None:
        entity = _entity("SaveTest")
        await backend.ontology_entities.save(entity)
        fetched = await backend.ontology_entities.get("SaveTest")
        assert fetched is not None
        assert fetched.name == "SaveTest"

    async def test_list_items_returns_all_in_order(
        self, backend: PersistenceBackend
    ) -> None:
        for name in ("Zebra", "Alpha", "Beta"):
            await backend.ontology_entities.register(_entity(name))
        items = await backend.ontology_entities.list_items(limit=10, offset=0)
        names = [e.name for e in items]
        assert {"Zebra", "Alpha", "Beta"} <= set(names)
        assert names.index("Alpha") < names.index("Beta") < names.index("Zebra")

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
        await backend.ontology_drift.append(_drift_report("Widget"))
        rows = await backend.ontology_drift.get_latest(NotBlankStr("Widget"))
        assert len(rows) >= 1
        assert rows[0].entity_name == "Widget"

    async def test_get_latest_honours_limit(self, backend: PersistenceBackend) -> None:
        for idx in range(5):
            await backend.ontology_drift.append(
                _drift_report("Repeated", divergence=idx / 10),
            )
        rows = await backend.ontology_drift.get_latest(NotBlankStr("Repeated"), limit=2)
        assert len(rows) <= 2

    async def test_query_returns_newest_first(
        self, backend: PersistenceBackend
    ) -> None:
        from synthorg.persistence.ontology_protocol import (
            DriftReportFilterSpec,
        )

        for idx in range(3):
            await backend.ontology_drift.append(
                _drift_report(f"QueryEnt{idx}", divergence=idx / 10),
            )
        rows = await backend.ontology_drift.query(DriftReportFilterSpec())
        names = [r.entity_name for r in rows]
        # Append-only contract: newest insertion first.
        assert names.index("QueryEnt2") < names.index("QueryEnt0")

    async def test_query_pagination(self, backend: PersistenceBackend) -> None:
        from synthorg.persistence.ontology_protocol import (
            DriftReportFilterSpec,
        )

        for idx in range(4):
            await backend.ontology_drift.append(
                _drift_report(f"PageEnt{idx}", divergence=idx / 10),
            )
        first = await backend.ontology_drift.query(
            DriftReportFilterSpec(), limit=2, offset=0
        )
        second = await backend.ontology_drift.query(
            DriftReportFilterSpec(), limit=2, offset=2
        )
        assert len(first) == 2
        assert len(second) == 2
        first_names = {r.entity_name for r in first}
        second_names = {r.entity_name for r in second}
        assert first_names.isdisjoint(second_names)

    async def test_purge_before_removes_old_reports(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.ontology_drift.append(_drift_report("Prunable"))
        # A far-future threshold strictly newer than every stored row
        # removes the lot; the return value reports the rows deleted.
        future = datetime(2999, 1, 1, tzinfo=UTC)
        removed = await backend.ontology_drift.purge_before(future)
        assert removed >= 1
        remaining = await backend.ontology_drift.get_latest(NotBlankStr("Prunable"))
        assert remaining == ()

    async def test_purge_before_keeps_newer_reports(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.ontology_drift.append(_drift_report("Kept"))
        # A past threshold older than every stored row deletes nothing.
        past = datetime(2000, 1, 1, tzinfo=UTC)
        removed = await backend.ontology_drift.purge_before(past)
        assert removed == 0
        remaining = await backend.ontology_drift.get_latest(NotBlankStr("Kept"))
        assert len(remaining) >= 1

    async def test_purge_before_rejects_naive_threshold(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            await backend.ontology_drift.purge_before(datetime(2026, 1, 1))  # noqa: DTZ001

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
        await backend.ontology_drift.append(
            _drift_report("EntityA", divergence=0.2),
        )
        await backend.ontology_drift.append(
            _drift_report("EntityA", divergence=0.3),
        )
        await backend.ontology_drift.append(
            _drift_report("EntityB", divergence=0.5),
        )
        rows = await backend.ontology_drift.get_all_latest()
        by_entity = {r.entity_name: r for r in rows}
        assert "EntityA" in by_entity
        assert "EntityB" in by_entity
        # Latest score for EntityA should be the most recent (0.3)
        assert by_entity["EntityA"].divergence_score == pytest.approx(0.3)
