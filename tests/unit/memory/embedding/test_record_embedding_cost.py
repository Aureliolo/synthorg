"""Cost attribution for one embedding call.

``record_embedding_cost`` is the only place an embedding call's spend
reaches the budget system, so its silent paths matter: a model litellm
has no price for must be reported rather than folded into a `0.0`
``CostRecord`` nobody can distinguish from a genuinely free call.
"""

from types import SimpleNamespace

import pytest
import structlog.testing

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.memory.embedding.dispatch import record_embedding_cost
from synthorg.observability.events.budget import (
    BUDGET_EMBEDDING_COST_RECORDED,
    BUDGET_EMBEDDING_MODEL_UNPRICED,
)
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_PROVIDER = "example-provider"
_MODEL = "example-basic-001"


def _response(*, response_cost: float | None, prompt_tokens: int = 10) -> object:
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=prompt_tokens),
        _hidden_params={"response_cost": response_cost},
    )


class TestUnpricedModelWarns:
    async def test_missing_response_cost_warns_and_records_zero(self) -> None:
        tracker = mock_of[CostTrackerProtocol](budget_config=None)
        with structlog.testing.capture_logs() as logs:
            await record_embedding_cost(
                _response(response_cost=None),
                cost_tracker=tracker,
                provider=_PROVIDER,
                model=_MODEL,
            )
        matches = [
            log for log in logs if log["event"] == BUDGET_EMBEDDING_MODEL_UNPRICED
        ]
        assert len(matches) == 1
        assert matches[0]["model"] == f"{_PROVIDER}/{_MODEL}"
        assert matches[0]["setting"] == "cost_per_1k_input/cost_per_1k_output"
        recorded = tracker.record.await_args.args[0]
        assert recorded.cost == 0.0

    async def test_genuinely_zero_cost_does_not_warn(self) -> None:
        tracker = mock_of[CostTrackerProtocol](budget_config=None)
        with structlog.testing.capture_logs() as logs:
            await record_embedding_cost(
                _response(response_cost=0.0),
                cost_tracker=tracker,
                provider=_PROVIDER,
                model=_MODEL,
            )
        events = [log["event"] for log in logs]
        assert BUDGET_EMBEDDING_MODEL_UNPRICED not in events


class TestSuccessfulRecordLogsRecorded:
    async def test_priced_response_logs_recorded_at_debug(self) -> None:
        tracker = mock_of[CostTrackerProtocol](budget_config=None)
        with structlog.testing.capture_logs() as logs:
            await record_embedding_cost(
                _response(response_cost=0.002),
                cost_tracker=tracker,
                provider=_PROVIDER,
                model=_MODEL,
            )
        events = [log["event"] for log in logs]
        assert BUDGET_EMBEDDING_COST_RECORDED in events
        recorded = tracker.record.await_args.args[0]
        assert recorded.cost == 0.002


class TestNoCostTracker:
    async def test_absent_tracker_is_a_no_op(self) -> None:
        await record_embedding_cost(
            _response(response_cost=None),
            cost_tracker=None,
            provider=_PROVIDER,
            model=_MODEL,
        )
