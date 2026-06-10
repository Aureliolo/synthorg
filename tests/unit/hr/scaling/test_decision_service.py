"""Unit tests for :class:`ScalingDecisionService`."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import structlog.testing

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.config import ScalingConfig
from synthorg.hr.scaling.decision_service import ScalingDecisionService
from synthorg.hr.scaling.enums import ScalingActionType, ScalingStrategyName
from synthorg.hr.scaling.models import ScalingDecision
from synthorg.observability.events.hr import HR_SCALING_CONTROLLER_INVALID_REQUEST
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)


def _decision(
    *,
    action: ScalingActionType = ScalingActionType.HIRE,
    strategy: ScalingStrategyName = ScalingStrategyName.WORKLOAD,
    decision_id: str | None = None,
) -> ScalingDecision:
    return ScalingDecision(
        id=as_uuid(decision_id) if decision_id else uuid4(),
        action_type=action,
        source_strategy=strategy,
        target_role=NotBlankStr("engineer"),
        target_department=NotBlankStr("engineering"),
        rationale=NotBlankStr("workload threshold exceeded"),
        confidence=0.9,
        signals=(),
        created_at=_NOW,
    )


class _FakeScalingService:
    """Minimal stub exposing the surface ``ScalingDecisionService`` uses."""

    def __init__(
        self,
        decisions: list[ScalingDecision] | None = None,
        config: ScalingConfig | None = None,
    ) -> None:
        self._decisions = list(decisions or [])
        self._config = config or ScalingConfig()
        self.evaluate_calls: list[tuple[str, ...]] = []
        self._evaluate_return: tuple[ScalingDecision, ...] = ()

    def queue_evaluate_result(
        self,
        result: tuple[ScalingDecision, ...],
    ) -> None:
        self._evaluate_return = result

    def get_recent_decisions(self) -> tuple[ScalingDecision, ...]:
        return tuple(self._decisions)

    @property
    def config(self) -> ScalingConfig:
        return self._config

    async def evaluate(
        self,
        *,
        agent_ids: tuple[NotBlankStr, ...],
    ) -> tuple[ScalingDecision, ...]:
        self.evaluate_calls.append(tuple(str(a) for a in agent_ids))
        return self._evaluate_return


class TestListDecisions:
    """Newest-first + pagination."""

    async def test_newest_first_with_total(self) -> None:
        # Append in chronological order (oldest first); deque
        # preserves insertion order so the first item is the oldest.
        decisions = [_decision(decision_id=f"d-{i}") for i in range(3)]
        svc = _FakeScalingService(decisions=decisions)
        service = ScalingDecisionService(scaling=svc)  # type: ignore[arg-type]

        page, total = await service.list_decisions(offset=0, limit=50)

        assert total == 3
        assert [d.id for d in page] == [
            as_uuid("d-2"),
            as_uuid("d-1"),
            as_uuid("d-0"),
        ]

    async def test_paginates(self) -> None:
        decisions = [_decision(decision_id=f"d-{i}") for i in range(5)]
        svc = _FakeScalingService(decisions=decisions)
        service = ScalingDecisionService(scaling=svc)  # type: ignore[arg-type]

        page, total = await service.list_decisions(offset=2, limit=2)

        assert total == 5
        assert [d.id for d in page] == [as_uuid("d-2"), as_uuid("d-1")]

    async def test_empty(self) -> None:
        svc = _FakeScalingService()
        service = ScalingDecisionService(scaling=svc)  # type: ignore[arg-type]

        page, total = await service.list_decisions(offset=0, limit=50)

        assert total == 0
        assert page == ()

    async def test_negative_offset_rejects(self) -> None:
        svc = _FakeScalingService()
        service = ScalingDecisionService(scaling=svc)  # type: ignore[arg-type]

        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(ValueError, match="offset"),
        ):
            await service.list_decisions(offset=-1, limit=1)
        assert any(
            e.get("event") == HR_SCALING_CONTROLLER_INVALID_REQUEST for e in events
        ), "invalid-request event must fire before the ValueError is raised"

    async def test_non_positive_limit_rejects(self) -> None:
        svc = _FakeScalingService()
        service = ScalingDecisionService(scaling=svc)  # type: ignore[arg-type]

        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(ValueError, match="limit"),
        ):
            await service.list_decisions(offset=0, limit=0)
        assert any(
            e.get("event") == HR_SCALING_CONTROLLER_INVALID_REQUEST for e in events
        ), "invalid-request event must fire before the ValueError is raised"


class TestGetDecision:
    async def test_returns_match(self) -> None:
        decisions = [_decision(decision_id="d-1"), _decision(decision_id="d-2")]
        svc = _FakeScalingService(decisions=decisions)
        service = ScalingDecisionService(scaling=svc)  # type: ignore[arg-type]

        result = await service.get_decision(sid("d-2"))

        assert result is not None
        assert result.id == as_uuid("d-2")

    async def test_returns_none_for_unknown(self) -> None:
        svc = _FakeScalingService(decisions=[_decision(decision_id="d-1")])
        service = ScalingDecisionService(scaling=svc)  # type: ignore[arg-type]

        result = await service.get_decision(sid("d-missing"))

        assert result is None


class TestGetConfig:
    async def test_returns_service_config(self) -> None:
        cfg = ScalingConfig(enabled=False)
        svc = _FakeScalingService(config=cfg)
        service = ScalingDecisionService(scaling=svc)  # type: ignore[arg-type]

        result = await service.get_config()

        assert result is cfg


class TestTrigger:
    async def test_empty_evaluate_result_returns_empty(self) -> None:
        svc = _FakeScalingService()
        svc.queue_evaluate_result(())
        service = ScalingDecisionService(scaling=svc)  # type: ignore[arg-type]

        result = await service.trigger((NotBlankStr("a-1"),))

        assert result == ()
        assert svc.evaluate_calls == [("a-1",)]

    async def test_delegates_and_returns_decisions(self) -> None:
        svc = _FakeScalingService()
        expected = (_decision(decision_id="d-1"),)
        svc.queue_evaluate_result(expected)
        service = ScalingDecisionService(scaling=svc)  # type: ignore[arg-type]

        result = await service.trigger((NotBlankStr("a-1"), NotBlankStr("a-2")))

        assert result == expected
        assert svc.evaluate_calls == [("a-1", "a-2")]
