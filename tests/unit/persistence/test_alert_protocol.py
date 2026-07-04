"""Unit tests for ``AlertFilterSpec``'s own construction invariants.

Complements ``tests/conformance/persistence/test_alert_repository.py``
(which exercises valid filter specs end-to-end against both backends)
with the one gap that suite doesn't cover: the inverted-window
rejection never gets an invalid ``since``/``until`` pair to reject.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.meta.models import RuleSeverity
from synthorg.persistence.alert_protocol import AlertFilterSpec

pytestmark = pytest.mark.unit

_T1 = datetime(2026, 1, 1, tzinfo=UTC)
_T2 = _T1 + timedelta(days=1)


class TestAlertFilterSpecWindowValidation:
    def test_since_before_until_is_valid(self) -> None:
        spec = AlertFilterSpec(since=_T1, until=_T2)
        assert spec.since == _T1
        assert spec.until == _T2

    def test_since_equal_to_until_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be earlier than"):
            AlertFilterSpec(since=_T1, until=_T1)

    def test_since_after_until_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be earlier than"):
            AlertFilterSpec(since=_T2, until=_T1)

    def test_since_alone_is_valid(self) -> None:
        spec = AlertFilterSpec(since=_T1)
        assert spec.since == _T1
        assert spec.until is None

    def test_until_alone_is_valid(self) -> None:
        spec = AlertFilterSpec(until=_T2)
        assert spec.until == _T2
        assert spec.since is None

    def test_neither_bound_is_valid(self) -> None:
        spec = AlertFilterSpec()
        assert spec.since is None
        assert spec.until is None


class TestAlertFilterSpecImmutability:
    def test_frozen_rejects_mutation(self) -> None:
        spec = AlertFilterSpec(severity=RuleSeverity.WARNING)
        with pytest.raises(ValidationError):
            spec.severity = RuleSeverity.CRITICAL  # type: ignore[misc]

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AlertFilterSpec(unknown_field="x")  # type: ignore[call-arg]
