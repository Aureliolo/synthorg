"""Unit tests for the terminal-resolution escalation builders.

The ``timeout_resolution`` / ``cancelled_resolution`` helpers stamp a
``resolved_at`` time.  These tests pin the optional ``clock`` seam so the
stamp is deterministic under an injected :class:`FakeClock`, and confirm
the default falls back to system wall-clock time.
"""

from datetime import UTC, datetime

import pytest

from synthorg.communication.conflict_resolution._escalation_builders import (
    cancelled_resolution,
    timeout_resolution,
)
from synthorg.communication.conflict_resolution.models import (
    ConflictResolutionOutcome,
)
from tests._shared import FakeClock

from .conftest import make_conflict

pytestmark = pytest.mark.unit

_FIXED = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)


def test_timeout_resolution_uses_injected_clock() -> None:
    """``resolved_at`` comes from the injected clock when provided."""
    resolution = timeout_resolution(make_conflict(), clock=FakeClock(start=_FIXED))
    assert resolution.outcome is ConflictResolutionOutcome.ESCALATED_TO_HUMAN
    assert resolution.resolved_at == _FIXED


def test_cancelled_resolution_uses_injected_clock() -> None:
    """``resolved_at`` comes from the injected clock when provided."""
    resolution = cancelled_resolution(make_conflict(), clock=FakeClock(start=_FIXED))
    assert resolution.outcome is ConflictResolutionOutcome.ESCALATED_TO_HUMAN
    assert resolution.resolved_at == _FIXED


def test_resolution_defaults_to_wall_clock() -> None:
    """Without a clock the builders stamp a timezone-aware UTC time."""
    before = datetime.now(UTC)
    resolution = timeout_resolution(make_conflict())
    after = datetime.now(UTC)
    assert resolution.resolved_at.tzinfo is not None
    assert before <= resolution.resolved_at <= after
