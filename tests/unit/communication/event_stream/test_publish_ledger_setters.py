"""Tests for the hot SSE replay history-bound setters on PublishLedger.

``EventStreamHistorySettingsSubscriber`` pushes operator edits to these bounds
onto the live ledger, so a change applies without a restart.
"""

import pytest

from synthorg.communication.event_stream._publish_ledger import PublishLedger

pytestmark = pytest.mark.unit


def _ledger() -> PublishLedger:
    """Build a ledger with documented default bounds."""
    return PublishLedger(
        history_max_sessions=1024,
        history_per_session=256,
        dedup_ttl_seconds=60.0,
        dedup_max_entries_per_session=1024,
    )


def test_set_history_max_sessions_updates_cap() -> None:
    """The LRU session cap is replaced in place."""
    ledger = _ledger()
    ledger.set_history_max_sessions(16)
    assert ledger._history_max_sessions == 16


def test_set_history_per_session_updates_depth() -> None:
    """The per-session ring depth applied to new sessions is replaced."""
    ledger = _ledger()
    ledger.set_history_per_session(32)
    assert ledger._history_per_session == 32


@pytest.mark.parametrize("value", [0, -1])
def test_set_history_max_sessions_rejects_non_positive(value: int) -> None:
    """A non-positive cap is rejected (the eviction loop needs >= 1)."""
    with pytest.raises(ValueError, match="history_max_sessions"):
        _ledger().set_history_max_sessions(value)


@pytest.mark.parametrize("value", [0, -1])
def test_set_history_per_session_rejects_non_positive(value: int) -> None:
    """A non-positive depth is rejected."""
    with pytest.raises(ValueError, match="history_per_session"):
        _ledger().set_history_per_session(value)
