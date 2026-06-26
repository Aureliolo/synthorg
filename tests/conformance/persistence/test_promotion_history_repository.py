"""Conformance tests for ``PromotionHistoryRepository``."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import PromotionDirection
from synthorg.hr.promotion.models import PromotionEvaluation, PromotionRecord
from synthorg.hr.seniority import SeniorityLevel
from synthorg.persistence.promotion_history_protocol import (
    PromotionHistoryFilterSpec,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)


def _evaluation(
    *,
    agent_id: str,
    direction: PromotionDirection,
    current: SeniorityLevel,
    target: SeniorityLevel,
) -> PromotionEvaluation:
    return PromotionEvaluation(
        agent_id=NotBlankStr(agent_id),
        current_level=current,
        target_level=target,
        direction=direction,
        required_criteria_met=True,
        eligible=True,
        evaluated_at=_NOW,
        strategy_name=NotBlankStr("threshold"),
    )


def _record(
    *,
    agent_id: str = "agent-alpha",
    direction: PromotionDirection = PromotionDirection.PROMOTION,
    when: datetime = _NOW,
) -> PromotionRecord:
    if direction is PromotionDirection.PROMOTION:
        old_level, new_level = SeniorityLevel.JUNIOR, SeniorityLevel.MID
    else:
        old_level, new_level = SeniorityLevel.SENIOR, SeniorityLevel.MID
    return PromotionRecord(
        agent_id=NotBlankStr(agent_id),
        agent_name=NotBlankStr(f"{agent_id} display"),
        old_level=old_level,
        new_level=new_level,
        direction=direction,
        evaluation=_evaluation(
            agent_id=agent_id,
            direction=direction,
            current=old_level,
            target=new_level,
        ),
        effective_at=when,
        initiated_by=NotBlankStr("system"),
    )


class TestPromotionHistoryRepository:
    async def test_append_and_query_newest_first(
        self, backend: PersistenceBackend
    ) -> None:
        r1 = _record(when=_NOW)
        r2 = _record(when=_NOW + timedelta(hours=2))
        await backend.promotion_history.append(r1)
        await backend.promotion_history.append(r2)

        results = await backend.promotion_history.query(
            PromotionHistoryFilterSpec(agent_id=NotBlankStr("agent-alpha"))
        )
        assert [r.id for r in results] == [r2.id, r1.id]
        # Full nested round-trip survived the JSON payload column.
        assert results[0].evaluation.strategy_name == "threshold"

    async def test_filter_by_direction(self, backend: PersistenceBackend) -> None:
        await backend.promotion_history.append(
            _record(direction=PromotionDirection.PROMOTION)
        )
        await backend.promotion_history.append(
            _record(
                direction=PromotionDirection.DEMOTION,
                when=_NOW + timedelta(hours=1),
            )
        )

        demotions = await backend.promotion_history.query(
            PromotionHistoryFilterSpec(
                agent_id=NotBlankStr("agent-alpha"),
                direction=PromotionDirection.DEMOTION,
            )
        )
        assert len(demotions) == 1
        assert demotions[0].direction is PromotionDirection.DEMOTION

    async def test_filter_since(self, backend: PersistenceBackend) -> None:
        await backend.promotion_history.append(_record(when=_NOW - timedelta(days=10)))
        await backend.promotion_history.append(_record(when=_NOW))

        recent = await backend.promotion_history.query(
            PromotionHistoryFilterSpec(
                agent_id=NotBlankStr("agent-alpha"),
                since=_NOW - timedelta(days=1),
            )
        )
        assert len(recent) == 1
        assert recent[0].effective_at == _NOW

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        await backend.promotion_history.append(_record(when=_NOW - timedelta(days=30)))
        await backend.promotion_history.append(_record(when=_NOW))

        removed = await backend.promotion_history.purge_before(_NOW - timedelta(days=1))
        assert removed == 1
        remaining = await backend.promotion_history.query(
            PromotionHistoryFilterSpec(agent_id=NotBlankStr("agent-alpha"))
        )
        assert [r.effective_at for r in remaining] == [_NOW]
