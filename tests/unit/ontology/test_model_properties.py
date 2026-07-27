"""Property-based tests for ontology models."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.ontology.models import (
    AgentDrift,
    DriftAction,
    DriftReport,
    EntityDefinition,
    EntityField,
    EntityRelation,
    EntitySource,
    EntityTier,
)

pytestmark = pytest.mark.unit

_not_blank = st.text(min_size=1, max_size=50).filter(lambda s: s.strip())
_entity_tiers = st.sampled_from(list(EntityTier))
_entity_sources = st.sampled_from(list(EntitySource))
_drift_actions = st.sampled_from(list(DriftAction))
_utc_datetimes = st.datetimes(
    min_value=datetime(2000, 1, 1),  # noqa: DTZ001 -- timezones arg adds tz
    max_value=datetime(2100, 1, 1),  # noqa: DTZ001
    timezones=st.just(UTC),
)
_divergence_scores = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)

_entity_fields_st = st.builds(
    EntityField,
    name=_not_blank,
    type_hint=_not_blank,
    description=st.text(max_size=100),
)

_entity_relations_st = st.builds(
    EntityRelation,
    target=_not_blank,
    relation=_not_blank,
    description=st.text(max_size=100),
)


def _unique_fields(
    fields: list[EntityField],
) -> tuple[EntityField, ...]:
    """Deduplicate fields by name, keeping first occurrence."""
    seen: set[str] = set()
    result: list[EntityField] = []
    for f in fields:
        if f.name not in seen:
            seen.add(f.name)
            result.append(f)
    return tuple(result)


def _unique_relationships(
    rels: list[EntityRelation],
) -> tuple[EntityRelation, ...]:
    """Deduplicate relationships by (target, relation) pair."""
    seen: set[tuple[str, str]] = set()
    result: list[EntityRelation] = []
    for r in rels:
        key = (r.target, r.relation)
        if key not in seen:
            seen.add(key)
            result.append(r)
    return tuple(result)


def _build_entity_definition(  # noqa: PLR0913
    *,
    name: str,
    tier: EntityTier,
    source: EntitySource,
    definition: str,
    fields: list[EntityField],
    constraints: list[str],
    disambiguation: str,
    relationships: list[EntityRelation],
    created_by: str,
    ts: datetime,
) -> EntityDefinition:
    return EntityDefinition(
        name=name,
        tier=tier,
        source=source,
        definition=definition,
        fields=_unique_fields(fields),
        constraints=tuple(constraints),
        disambiguation=disambiguation,
        relationships=_unique_relationships(relationships),
        created_by=created_by,
        created_at=ts,
        updated_at=ts,
    )


_entity_definition_st = st.builds(
    _build_entity_definition,
    name=_not_blank,
    tier=_entity_tiers,
    source=_entity_sources,
    definition=st.text(max_size=200),
    fields=st.lists(_entity_fields_st, max_size=50),
    constraints=st.lists(st.text(max_size=50), max_size=10),
    disambiguation=st.text(max_size=100),
    relationships=st.lists(_entity_relations_st, max_size=10),
    created_by=_not_blank,
    ts=_utc_datetimes,
)


class TestEntityDefinitionProperties:
    """Property-based tests for EntityDefinition."""

    @given(entity=_entity_definition_st)
    def test_model_dump_validate_roundtrip(
        self,
        entity: EntityDefinition,
    ) -> None:
        dumped = entity.model_dump(mode="json")
        restored = EntityDefinition.model_validate(dumped)
        assert restored == entity

    @given(entity=_entity_definition_st)
    def test_json_roundtrip(
        self,
        entity: EntityDefinition,
    ) -> None:
        json_str = entity.model_dump_json()
        restored = EntityDefinition.model_validate_json(json_str)
        assert restored == entity

    @given(
        fields=st.lists(_entity_fields_st, min_size=0, max_size=50),
    )
    def test_arbitrary_field_counts(
        self,
        fields: list[EntityField],
    ) -> None:
        unique = _unique_fields(fields)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        entity = EntityDefinition(
            name="Test",
            tier=EntityTier.CORE,
            source=EntitySource.AUTO,
            fields=unique,
            created_by="test",
            created_at=now,
            updated_at=now,
        )
        assert entity.fields == unique


class TestDriftReportProperties:
    """Property-based tests for DriftReport."""

    @given(
        score=_divergence_scores,
        version=st.integers(min_value=1, max_value=10000),
        action=_drift_actions,
    )
    def test_valid_scores_accepted(
        self,
        score: float,
        version: int,
        action: DriftAction,
    ) -> None:
        report = DriftReport(
            entity_name="Test",
            divergence_score=score,
            canonical_version=version,
            recommendation=action,
        )
        assert 0.0 <= report.divergence_score <= 1.0

    @given(
        agents=st.lists(
            st.builds(
                AgentDrift,
                agent_id=_not_blank,
                divergence_score=_divergence_scores,
                details=st.text(max_size=50),
            ),
            max_size=10,
        ),
    )
    def test_arbitrary_agent_counts(
        self,
        agents: list[AgentDrift],
    ) -> None:
        report = DriftReport(
            entity_name="Test",
            divergence_score=0.5,
            divergent_agents=tuple(agents),
            canonical_version=1,
            recommendation=DriftAction.NOTIFY,
        )
        assert len(report.divergent_agents) == len(agents)
