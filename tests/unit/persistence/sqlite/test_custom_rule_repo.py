"""Tests for SQLiteCustomRuleRepository."""

from datetime import UTC, datetime
from uuid import uuid4

import aiosqlite
import pytest

from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.meta.models import ProposalAltitude, RuleSeverity
from synthorg.meta.rules.custom import Comparator, CustomRuleDefinition
from synthorg.persistence.custom_rule_protocol import CustomRuleFilterSpec
from synthorg.persistence.sqlite.custom_rule_repo import (
    SQLiteCustomRuleRepository,
)
from tests._shared.persistence import make_private_write_context

pytestmark = pytest.mark.unit


@pytest.fixture
def repo(
    migrated_db: aiosqlite.Connection,
) -> SQLiteCustomRuleRepository:
    return SQLiteCustomRuleRepository(
        migrated_db, write_context=make_private_write_context()
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _make_rule(  # noqa: PLR0913
    *,
    name: str = "test-rule",
    metric_path: str = "performance.avg_quality_score",
    comparator: Comparator = Comparator.LT,
    threshold: float = 5.0,
    severity: RuleSeverity = RuleSeverity.WARNING,
    enabled: bool = True,
) -> CustomRuleDefinition:
    now = _now()
    return CustomRuleDefinition(
        id=uuid4(),
        name=name,
        description=f"Test rule: {name}",
        metric_path=metric_path,
        comparator=comparator,
        threshold=threshold,
        severity=severity,
        target_altitudes=(ProposalAltitude.CONFIG_TUNING,),
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


class TestSQLiteCustomRuleRepository:
    """Tests for the SQLite custom rule repository."""

    async def test_save_and_get(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        rule = _make_rule()
        await repo.save(rule)
        result = await repo.get(str(rule.id))
        assert result is not None
        assert result.name == rule.name
        assert result.metric_path == rule.metric_path
        assert result.comparator == rule.comparator
        assert result.threshold == rule.threshold
        assert result.severity == rule.severity
        assert result.enabled == rule.enabled
        assert result.target_altitudes == rule.target_altitudes

    async def test_get_returns_none_for_missing(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        result = await repo.get(str(uuid4()))
        assert result is None

    async def test_get_by_name(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        rule = _make_rule(name="named-rule")
        await repo.save(rule)
        result = await repo.get_by_name("named-rule")
        assert result is not None
        assert result.id == rule.id

    async def test_get_by_name_returns_none(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        result = await repo.get_by_name("nonexistent")
        assert result is None

    async def test_list_rules_all(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        rule_a = _make_rule(name="alpha-rule")
        rule_b = _make_rule(name="beta-rule")
        await repo.save(rule_a)
        await repo.save(rule_b)
        rules = await repo.query(CustomRuleFilterSpec())
        assert len(rules) == 2
        assert rules[0].name == "alpha-rule"
        assert rules[1].name == "beta-rule"

    async def test_list_rules_enabled_only(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        enabled_rule = _make_rule(name="enabled-rule", enabled=True)
        disabled_rule = _make_rule(name="disabled-rule", enabled=False)
        await repo.save(enabled_rule)
        await repo.save(disabled_rule)
        enabled = await repo.query(CustomRuleFilterSpec(enabled_only=True))
        assert len(enabled) == 1
        assert enabled[0].name == "enabled-rule"

    async def test_list_rules_empty(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        rules = await repo.query(CustomRuleFilterSpec())
        assert rules == ()

    async def test_delete_found(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        rule = _make_rule()
        await repo.save(rule)
        deleted = await repo.delete(str(rule.id))
        assert deleted is True
        result = await repo.get(str(rule.id))
        assert result is None

    async def test_delete_not_found(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        deleted = await repo.delete(str(uuid4()))
        assert deleted is False

    async def test_save_duplicate_name_raises(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        rule_a = _make_rule(name="duplicate")
        rule_b = _make_rule(name="duplicate")
        await repo.save(rule_a)
        with pytest.raises(ConstraintViolationError, match="already exists"):
            await repo.save(rule_b)

    async def test_save_upsert_same_id(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        rule = _make_rule(name="original")
        await repo.save(rule)
        updated = rule.model_copy(
            update={
                "name": "updated-name",
                "threshold": 9.0,
                "updated_at": _now(),
            },
        )
        await repo.save(updated)
        result = await repo.get(str(rule.id))
        assert result is not None
        assert result.name == "updated-name"
        assert result.threshold == 9.0

    async def test_multiple_target_altitudes_round_trip(
        self,
        repo: SQLiteCustomRuleRepository,
    ) -> None:
        rule = CustomRuleDefinition(
            id=uuid4(),
            name="multi-altitude",
            description="Rule with multiple altitudes",
            metric_path="performance.avg_quality_score",
            comparator=Comparator.LT,
            threshold=5.0,
            severity=RuleSeverity.WARNING,
            target_altitudes=(
                ProposalAltitude.CONFIG_TUNING,
                ProposalAltitude.ARCHITECTURE,
                ProposalAltitude.PROMPT_TUNING,
            ),
            created_at=_now(),
            updated_at=_now(),
        )
        await repo.save(rule)
        result = await repo.get(str(rule.id))
        assert result is not None
        assert result.target_altitudes == (
            ProposalAltitude.CONFIG_TUNING,
            ProposalAltitude.ARCHITECTURE,
            ProposalAltitude.PROMPT_TUNING,
        )
