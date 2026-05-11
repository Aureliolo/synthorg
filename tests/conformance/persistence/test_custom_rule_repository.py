"""Parametrized conformance tests for ``CustomRuleRepository``.

Runs against both SQLite and Postgres via the ``backend`` fixture so
SQLite-vs-Postgres divergence is caught on every commit.  Both backends
store ``target_altitudes`` as a JSON-encoded array (TEXT on SQLite,
JSONB on Postgres) and treat ``enabled`` as INTEGER 0/1 vs BOOLEAN
respectively, but the protocol surface is identical.  The tests assert
the contract of the shared helpers in
``synthorg.persistence._shared.custom_rule`` -- altitude round-trip,
UTC timestamp normalisation, and enum coercion.
"""

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import AwareDatetime

from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import ProposalAltitude, RuleSeverity
from synthorg.meta.rules.custom import Comparator, CustomRuleDefinition
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _ts() -> AwareDatetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _make_rule(  # noqa: PLR0913
    *,
    rule_id: UUID | None = None,
    name: str = "test-rule",
    metric_path: str = "performance.avg_quality_score",
    comparator: Comparator = Comparator.LT,
    threshold: float = 5.0,
    severity: RuleSeverity = RuleSeverity.WARNING,
    enabled: bool = True,
    target_altitudes: tuple[ProposalAltitude, ...] = (ProposalAltitude.CONFIG_TUNING,),
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> CustomRuleDefinition:
    base_ts = _ts()
    return CustomRuleDefinition(
        id=rule_id or uuid4(),
        name=name,
        description=f"Test rule: {name}",
        metric_path=metric_path,
        comparator=comparator,
        threshold=threshold,
        severity=severity,
        target_altitudes=target_altitudes,
        enabled=enabled,
        created_at=created_at or base_ts,
        updated_at=updated_at or base_ts,
    )


class TestCustomRuleRepositoryConformance:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        rule = _make_rule()
        await backend.custom_rules.save(rule)

        fetched = await backend.custom_rules.get(NotBlankStr(str(rule.id)))
        assert fetched is not None
        assert fetched.id == rule.id
        assert fetched.name == rule.name
        assert fetched.metric_path == rule.metric_path
        assert fetched.comparator is Comparator.LT
        assert fetched.threshold == 5.0
        assert fetched.severity is RuleSeverity.WARNING
        assert fetched.enabled is True
        assert fetched.target_altitudes == (ProposalAltitude.CONFIG_TUNING,)

    async def test_get_returns_none_for_missing(
        self, backend: PersistenceBackend
    ) -> None:
        result = await backend.custom_rules.get(NotBlankStr(str(uuid4())))
        assert result is None

    async def test_get_by_name(self, backend: PersistenceBackend) -> None:
        rule = _make_rule(name="named-rule")
        await backend.custom_rules.save(rule)

        fetched = await backend.custom_rules.get_by_name(
            NotBlankStr("named-rule"),
        )
        assert fetched is not None
        assert fetched.id == rule.id

    async def test_get_by_name_returns_none(self, backend: PersistenceBackend) -> None:
        result = await backend.custom_rules.get_by_name(
            NotBlankStr("nonexistent"),
        )
        assert result is None

    async def test_list_rules_orders_by_name(self, backend: PersistenceBackend) -> None:
        await backend.custom_rules.save(_make_rule(name="zebra"))
        await backend.custom_rules.save(_make_rule(name="alpha"))
        await backend.custom_rules.save(_make_rule(name="middle"))

        rows = await backend.custom_rules.list_rules()
        assert [r.name for r in rows] == ["alpha", "middle", "zebra"]

    async def test_list_rules_enabled_only(self, backend: PersistenceBackend) -> None:
        await backend.custom_rules.save(_make_rule(name="on", enabled=True))
        await backend.custom_rules.save(_make_rule(name="off", enabled=False))

        rows = await backend.custom_rules.list_rules(enabled_only=True)
        assert {r.name for r in rows} == {"on"}

    async def test_list_rules_respects_limit(self, backend: PersistenceBackend) -> None:
        for i in range(5):
            await backend.custom_rules.save(_make_rule(name=f"rule-lim-{i:02d}"))

        rows = await backend.custom_rules.list_rules(limit=3)
        assert len(rows) <= 3

    async def test_list_rules_empty(self, backend: PersistenceBackend) -> None:
        rows = await backend.custom_rules.list_rules()
        assert rows == ()

    async def test_delete_found_returns_true(self, backend: PersistenceBackend) -> None:
        rule = _make_rule()
        await backend.custom_rules.save(rule)
        assert await backend.custom_rules.delete(NotBlankStr(str(rule.id))) is True
        assert await backend.custom_rules.get(NotBlankStr(str(rule.id))) is None

    async def test_delete_missing_returns_false(
        self, backend: PersistenceBackend
    ) -> None:
        assert await backend.custom_rules.delete(NotBlankStr(str(uuid4()))) is False

    async def test_save_duplicate_name_raises_constraint(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.custom_rules.save(_make_rule(name="dup"))
        with pytest.raises(ConstraintViolationError):
            await backend.custom_rules.save(_make_rule(name="dup"))

    async def test_save_upsert_same_id(self, backend: PersistenceBackend) -> None:
        rule = _make_rule(name="original")
        await backend.custom_rules.save(rule)

        updated = rule.model_copy(
            update={
                "name": "renamed",
                "threshold": 9.0,
                "enabled": False,
                "updated_at": datetime(2026, 6, 1, tzinfo=UTC),
            },
        )
        await backend.custom_rules.save(updated)

        fetched = await backend.custom_rules.get(NotBlankStr(str(rule.id)))
        assert fetched is not None
        assert fetched.name == "renamed"
        assert fetched.threshold == 9.0
        assert fetched.enabled is False

    async def test_target_altitudes_round_trip_preserves_order(
        self, backend: PersistenceBackend
    ) -> None:
        # JSONB array on Postgres, TEXT(json.dumps) on SQLite.  Round
        # trip a multi-element tuple to assert order + element identity.
        rule = _make_rule(
            name="multi-altitude",
            target_altitudes=(
                ProposalAltitude.PROMPT_TUNING,
                ProposalAltitude.CONFIG_TUNING,
                ProposalAltitude.ARCHITECTURE,
            ),
        )
        await backend.custom_rules.save(rule)

        fetched = await backend.custom_rules.get(NotBlankStr(str(rule.id)))
        assert fetched is not None
        assert fetched.target_altitudes == (
            ProposalAltitude.PROMPT_TUNING,
            ProposalAltitude.CONFIG_TUNING,
            ProposalAltitude.ARCHITECTURE,
        )

    async def test_non_utc_timestamps_normalised_on_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        # Assert tzinfo AND the exact UTC instant for both timestamps;
        # a backend that incorrectly shifted the wall-clock time but
        # kept the offset would still satisfy a tzinfo-only check.
        offset_tz = timezone(timedelta(hours=5))
        local_now = datetime(2026, 4, 24, 17, 0, tzinfo=offset_tz)
        rule = _make_rule(
            name="tz-rule",
            created_at=local_now,
            updated_at=local_now,
        )
        await backend.custom_rules.save(rule)

        fetched = await backend.custom_rules.get(NotBlankStr(str(rule.id)))
        assert fetched is not None
        expected_utc = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
        assert fetched.created_at.tzinfo == UTC
        assert fetched.updated_at.tzinfo == UTC
        assert fetched.created_at == expected_utc
        assert fetched.updated_at == expected_utc

    async def test_comparator_round_trip_each_value(
        self, backend: PersistenceBackend
    ) -> None:
        # Every Comparator enum variant must serialise + parse back
        # identically; INTEGER 0/1 (SQLite) vs BOOLEAN (Postgres) is
        # orthogonal but exercised in the ``enabled`` field above.
        for comp in Comparator:
            rule = _make_rule(name=f"rule-{comp.value}", comparator=comp)
            await backend.custom_rules.save(rule)

        rows = await backend.custom_rules.list_rules()
        comparators = {r.comparator for r in rows}
        assert comparators == set(Comparator)
