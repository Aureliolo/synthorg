"""Cost-recording health reports a standing fault, not every blip.

Recording is best-effort so a lost record never fails the user's LLM call.
That makes a single failure uninteresting and a run of them serious: the spend
happens either way, so a persistent fault means the budget under-reports for
as long as it lasts, and some causes never clear on their own.
"""

from collections.abc import Iterator

import pytest

from synthorg.api.controllers._cost_recording_health import (
    CostRecordingState,
    resolve_cost_recording_health,
)
from synthorg.providers import cost_recording
from synthorg.providers.cost_recording import COST_FAILURE_ESCALATION_STREAK

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_streak() -> Iterator[None]:
    cost_recording._consecutive_cost_failures = 0
    yield
    cost_recording._consecutive_cost_failures = 0


def test_clean_recorder_reports_ok() -> None:
    health = resolve_cost_recording_health()
    assert health.state is CostRecordingState.OK
    assert health.dropped_records == 0
    assert health.detail is None


def test_short_run_of_failures_is_still_ok() -> None:
    # Below the threshold there is nothing to distinguish a blip from the
    # start of a fault, and reporting degraded on the first one would make
    # the card cry wolf on ordinary transient behaviour.
    for _ in range(COST_FAILURE_ESCALATION_STREAK - 1):
        cost_recording._note_cost_failure(reason="test")
    health = resolve_cost_recording_health()
    assert health.state is CostRecordingState.OK
    assert health.dropped_records == COST_FAILURE_ESCALATION_STREAK - 1


def test_sustained_failure_reports_degraded_with_a_count() -> None:
    for _ in range(COST_FAILURE_ESCALATION_STREAK):
        cost_recording._note_cost_failure(reason="test")
    health = resolve_cost_recording_health()
    assert health.state is CostRecordingState.DEGRADED
    assert health.dropped_records == COST_FAILURE_ESCALATION_STREAK
    # The operator needs to know the totals are wrong, not just that
    # something failed.
    assert health.detail is not None
    assert "spend" in health.detail


def test_a_landed_record_returns_the_card_to_ok() -> None:
    for _ in range(COST_FAILURE_ESCALATION_STREAK):
        cost_recording._note_cost_failure(reason="test")
    cost_recording._note_cost_success()
    health = resolve_cost_recording_health()
    assert health.state is CostRecordingState.OK
    assert health.dropped_records == 0
