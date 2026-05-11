"""Unit tests for :class:`CustomRulesService`.

Conformance tests cover the end-to-end repository contract against
SQLite + Postgres. These unit tests focus on the service's in-process
logic: NotFound surfacing, partial-update merge + validate behaviour,
and toggle semantics -- hit via a minimal in-memory repository fake
so each case runs in milliseconds.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import ProposalAltitude, RuleSeverity
from synthorg.meta.rules.custom import Comparator, CustomRuleDefinition
from synthorg.meta.rules.service import CustomRuleNotFoundError, CustomRulesService
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT
from synthorg.persistence._shared.pagination import validate_pagination_args

pytestmark = pytest.mark.unit


class _FakeCustomRuleRepository:
    """Minimal in-memory :class:`CustomRuleRepository` implementation."""

    def __init__(self) -> None:
        self._rows: dict[str, CustomRuleDefinition] = {}

    async def save(self, rule: CustomRuleDefinition) -> None:
        self._rows[str(rule.id)] = rule

    async def get(self, rule_id: NotBlankStr) -> CustomRuleDefinition | None:
        return self._rows.get(str(rule_id))

    async def get_by_name(self, name: NotBlankStr) -> CustomRuleDefinition | None:
        for row in self._rows.values():
            if row.name == name:
                return row
        return None

    async def list_rules(
        self,
        *,
        enabled_only: bool = False,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[CustomRuleDefinition, ...]:
        limit = validate_pagination_args(
            limit, offset=0, event="fake.custom_rules.list_rules"
        )
        rows = [r for r in self._rows.values() if not enabled_only or r.enabled]
        return tuple(sorted(rows, key=lambda r: r.name)[:limit])

    async def delete(self, rule_id: NotBlankStr) -> bool:
        return self._rows.pop(str(rule_id), None) is not None


def _rule(
    *,
    name: str = "slow-tasks",
    threshold: float = 5.0,
    enabled: bool = True,
) -> CustomRuleDefinition:
    now = datetime.now(UTC)
    return CustomRuleDefinition(
        id=uuid4(),
        name=NotBlankStr(name),
        description=NotBlankStr("Flag tasks below quality threshold"),
        metric_path=NotBlankStr("performance.avg_quality_score"),
        comparator=Comparator.LT,
        threshold=threshold,
        severity=RuleSeverity.WARNING,
        target_altitudes=(ProposalAltitude.CONFIG_TUNING,),
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


class TestCustomRulesServiceCRUD:
    async def test_get_missing_returns_none(self) -> None:
        service = CustomRulesService(repo=_FakeCustomRuleRepository())
        ghost = NotBlankStr("00000000-0000-0000-0000-000000000000")
        assert await service.get(ghost) is None

    async def test_create_then_list(self) -> None:
        service = CustomRulesService(repo=_FakeCustomRuleRepository())
        rule = _rule(name="quality-watch")
        await service.create(rule)
        page, total = await service.list_rules()
        assert total == 1
        assert len(page) == 1
        assert page[0].name == "quality-watch"

    @pytest.mark.parametrize(
        ("seed_count", "requested_limit"),
        [(5, 2), (5, 3), (10, 1), (3, 10)],
    )
    async def test_list_rules_respects_limit(
        self, seed_count: int, requested_limit: int
    ) -> None:
        """``list_rules(limit=N)`` truncates to N when more rules exist."""
        service = CustomRulesService(repo=_FakeCustomRuleRepository())
        for i in range(seed_count):
            await service.create(_rule(name=f"rule-{i:02d}"))

        page, total = await service.list_rules(limit=requested_limit)
        assert total == seed_count
        assert len(page) <= requested_limit
        assert len(page) == min(seed_count, requested_limit)

    async def test_delete_missing_raises(self) -> None:
        service = CustomRulesService(repo=_FakeCustomRuleRepository())
        with pytest.raises(CustomRuleNotFoundError):
            await service.delete(NotBlankStr("00000000-0000-0000-0000-000000000000"))

    async def test_delete_existing_returns_none_and_removes(self) -> None:
        service = CustomRulesService(repo=_FakeCustomRuleRepository())
        rule = _rule()
        await service.create(rule)
        await service.delete(NotBlankStr(str(rule.id)))
        assert await service.get(NotBlankStr(str(rule.id))) is None


class TestCustomRulesServiceUpdate:
    async def test_update_missing_raises(self) -> None:
        service = CustomRulesService(repo=_FakeCustomRuleRepository())
        with pytest.raises(CustomRuleNotFoundError):
            await service.update(
                NotBlankStr("00000000-0000-0000-0000-000000000000"),
                {"threshold": 9.0},
            )

    async def test_update_merges_partial_payload(self) -> None:
        service = CustomRulesService(repo=_FakeCustomRuleRepository())
        rule = _rule(threshold=5.0)
        await service.create(rule)

        updated = await service.update(
            NotBlankStr(str(rule.id)),
            {"threshold": 9.5, "severity": RuleSeverity.CRITICAL},
        )

        assert updated.threshold == 9.5
        assert updated.severity is RuleSeverity.CRITICAL
        # Unmodified fields preserved verbatim.
        assert updated.name == rule.name
        assert updated.metric_path == rule.metric_path
        assert updated.comparator is rule.comparator
        # updated_at moves forward.
        assert updated.updated_at >= rule.updated_at

    async def test_update_invalid_metric_path_raises(self) -> None:
        service = CustomRulesService(repo=_FakeCustomRuleRepository())
        rule = _rule()
        await service.create(rule)

        with pytest.raises(ValueError, match="metric_path"):
            await service.update(
                NotBlankStr(str(rule.id)),
                {"metric_path": "does.not.exist"},
            )

    @pytest.mark.parametrize(
        "immutable_field",
        ["id", "created_at"],
    )
    async def test_update_rejects_immutable_fields(
        self,
        immutable_field: str,
    ) -> None:
        """Callers cannot rewrite ``id`` / ``created_at`` via ``update``.

        Merging the caller payload verbatim into the persistence row
        would let an update turn into an identity change or an audit-
        history rewrite; the service must reject the request before any
        persistence call fires.
        """
        service = CustomRulesService(repo=_FakeCustomRuleRepository())
        rule = _rule()
        await service.create(rule)

        override = {
            "id": "00000000-0000-0000-0000-000000000099",
            "created_at": datetime(2020, 1, 1, tzinfo=UTC),
        }[immutable_field]

        with pytest.raises(ValueError, match=immutable_field):
            await service.update(
                NotBlankStr(str(rule.id)),
                {immutable_field: override},
            )


class TestCustomRulesServiceToggle:
    async def test_toggle_flips_enabled(self) -> None:
        service = CustomRulesService(repo=_FakeCustomRuleRepository())
        rule = _rule(enabled=True)
        await service.create(rule)

        toggled = await service.toggle(NotBlankStr(str(rule.id)))
        assert toggled.enabled is False

        toggled_back = await service.toggle(NotBlankStr(str(rule.id)))
        assert toggled_back.enabled is True

    async def test_toggle_missing_raises(self) -> None:
        service = CustomRulesService(repo=_FakeCustomRuleRepository())
        with pytest.raises(CustomRuleNotFoundError):
            await service.toggle(NotBlankStr("00000000-0000-0000-0000-000000000000"))
