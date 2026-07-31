"""Tests for the backup-coverage verdict behind ``/health``.

A deployment with no backup coverage serves every request correctly, so the
fault is invisible until someone needs a recovery point. Reporting only that
coverage is absent is barely better: the cause is decided once at boot, inside
a handler nothing else can see, so without carrying it an operator cannot tell
a missing binary from a path they mistyped.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from synthorg.api.controllers._backup_health import (
    BackupState,
    resolve_backup_health,
)
from synthorg.api.state import AppState
from synthorg.backup.service import BackupService
from tests._shared import mock_of


def _app_state(
    *,
    service: object,
    expected: bool,
    unavailable_reason: str | None = None,
) -> AppState:
    app_state = MagicMock(spec=AppState)
    app_state.slice.return_value = SimpleNamespace(
        service=service,
        expected=expected,
        unavailable_reason=unavailable_reason,
    )
    return app_state


@pytest.mark.unit
class TestResolveBackupHealth:
    """The three states a boot can leave backup coverage in."""

    def test_wired_service_reports_coverage_with_no_remedy(self) -> None:
        health = resolve_backup_health(
            _app_state(service=mock_of[BackupService](), expected=True)
        )

        assert health.state is BackupState.WIRED
        assert health.detail is None

    def test_never_attempted_is_not_a_verdict_about_backups(self) -> None:
        # Distinct from a failure: nothing was wanted, so there is nothing to
        # tell the operator to fix.
        health = resolve_backup_health(_app_state(service=None, expected=False))

        assert health.state is BackupState.UNATTEMPTED
        assert health.detail is None

    def test_absent_carries_the_cause_recorded_at_boot(self) -> None:
        # The reason is the whole point: "absent" alone sends an operator to
        # read startup logs to learn something the response already knows.
        health = resolve_backup_health(
            _app_state(
                service=None,
                expected=True,
                unavailable_reason="pg_dump is not available on PATH",
            )
        )

        assert health.state is BackupState.ABSENT
        assert health.detail is not None
        assert "pg_dump is not available on PATH" in health.detail

    def test_absent_without_a_recorded_cause_still_states_the_consequence(
        self,
    ) -> None:
        # A service that built and then failed to start leaves no construction
        # reason behind, and the operator still needs to know coverage is gone.
        health = resolve_backup_health(_app_state(service=None, expected=True))

        assert health.state is BackupState.ABSENT
        assert health.detail is not None
        assert "no recovery points" in health.detail
