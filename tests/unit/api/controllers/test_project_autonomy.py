"""Unit tests for the project autonomy-mode guard and audit helpers.

These lock the EFFECTIVE-mode behaviour: the guard and audit key off the
resolved mode (inheritance applied), so clearing an override that inherits
``full`` is treated identically to setting ``full`` outright.
"""

import pytest
import structlog.testing
from structlog.typing import EventDict

from synthorg.api.controllers._project_autonomy import (
    AutonomyModeTransition,
    audit_autonomy_mode_change,
    guard_full_autonomy_optin,
)
from synthorg.core.auth.roles import HumanRole
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.domain_errors import ForbiddenError, ValidationError

pytestmark = pytest.mark.unit

_AUDIT_EVENT = "api.project.autonomy_mode_changed"


def _transition(
    *,
    previous: AutonomyLevel | None,
    new: AutonomyLevel | None,
    effective_previous: AutonomyLevel,
    effective_new: AutonomyLevel,
) -> AutonomyModeTransition:
    return AutonomyModeTransition(
        previous=previous,
        new=new,
        effective_previous=effective_previous,
        effective_new=effective_new,
    )


class TestGuardFullAutonomyOptin:
    def test_ceo_confirmed_optin_is_allowed(self) -> None:
        guard_full_autonomy_optin(
            role=HumanRole.CEO,
            transition=_transition(
                previous=AutonomyLevel.SUPERVISED,
                new=AutonomyLevel.FULL,
                effective_previous=AutonomyLevel.SUPERVISED,
                effective_new=AutonomyLevel.FULL,
            ),
            confirm=True,
        )

    def test_non_ceo_optin_is_forbidden(self) -> None:
        with pytest.raises(ForbiddenError):
            guard_full_autonomy_optin(
                role=HumanRole.MANAGER,
                transition=_transition(
                    previous=AutonomyLevel.SUPERVISED,
                    new=AutonomyLevel.FULL,
                    effective_previous=AutonomyLevel.SUPERVISED,
                    effective_new=AutonomyLevel.FULL,
                ),
                confirm=True,
            )

    def test_ceo_optin_without_confirm_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            guard_full_autonomy_optin(
                role=HumanRole.CEO,
                transition=_transition(
                    previous=AutonomyLevel.SUPERVISED,
                    new=AutonomyLevel.FULL,
                    effective_previous=AutonomyLevel.SUPERVISED,
                    effective_new=AutonomyLevel.FULL,
                ),
                confirm=False,
            )

    def test_clear_into_inherited_full_is_guarded(self) -> None:
        # Clearing an override (literal None) whose company default is ``full``
        # resolves to an effective full and must still demand the CEO opt-in.
        with pytest.raises(ForbiddenError):
            guard_full_autonomy_optin(
                role=HumanRole.MANAGER,
                transition=_transition(
                    previous=AutonomyLevel.LOCKED,
                    new=None,
                    effective_previous=AutonomyLevel.LOCKED,
                    effective_new=AutonomyLevel.FULL,
                ),
                confirm=True,
            )

    def test_already_full_lateral_is_unguarded(self) -> None:
        # Effective full -> full is not an opt-in; no ceremony required.
        guard_full_autonomy_optin(
            role=HumanRole.MANAGER,
            transition=_transition(
                previous=AutonomyLevel.FULL,
                new=AutonomyLevel.FULL,
                effective_previous=AutonomyLevel.FULL,
                effective_new=AutonomyLevel.FULL,
            ),
            confirm=False,
        )

    def test_tightening_is_unguarded(self) -> None:
        guard_full_autonomy_optin(
            role=HumanRole.MANAGER,
            transition=_transition(
                previous=AutonomyLevel.FULL,
                new=AutonomyLevel.LOCKED,
                effective_previous=AutonomyLevel.FULL,
                effective_new=AutonomyLevel.LOCKED,
            ),
            confirm=False,
        )


class TestAuditAutonomyModeChange:
    def test_clear_into_inherited_full_records_gate_off(self) -> None:
        # Override cleared (literal None), effective resolves to full: the
        # audit must reflect the gate-off reality, not the None literal.
        with structlog.testing.capture_logs() as logs:
            audit_autonomy_mode_change(
                project_id="proj-1",
                transition=_transition(
                    previous=AutonomyLevel.LOCKED,
                    new=None,
                    effective_previous=AutonomyLevel.LOCKED,
                    effective_new=AutonomyLevel.FULL,
                ),
                requested_by="ceo-user",
            )
        entry = _single_audit(logs)
        assert entry["new_mode"] is None
        assert entry["effective_mode"] == "full"
        assert entry["gate_disabled"] is True
        assert entry["log_level"] == "warning"

    def test_lateral_full_is_not_newly_gate_off(self) -> None:
        # Already effective-full stays full: gate is off, but this is not a
        # NEW gate-off transition, so it logs at INFO.
        with structlog.testing.capture_logs() as logs:
            audit_autonomy_mode_change(
                project_id="proj-1",
                transition=_transition(
                    previous=None,
                    new=AutonomyLevel.FULL,
                    effective_previous=AutonomyLevel.FULL,
                    effective_new=AutonomyLevel.FULL,
                ),
                requested_by="ceo-user",
            )
        entry = _single_audit(logs)
        assert entry["gate_disabled"] is True
        assert entry["log_level"] == "info"

    def test_tightening_records_gate_on(self) -> None:
        with structlog.testing.capture_logs() as logs:
            audit_autonomy_mode_change(
                project_id="proj-1",
                transition=_transition(
                    previous=AutonomyLevel.FULL,
                    new=AutonomyLevel.LOCKED,
                    effective_previous=AutonomyLevel.FULL,
                    effective_new=AutonomyLevel.LOCKED,
                ),
                requested_by="ceo-user",
            )
        entry = _single_audit(logs)
        assert entry["previous_mode"] == "full"
        assert entry["new_mode"] == "locked"
        assert entry["gate_disabled"] is False
        assert entry["log_level"] == "info"


def _single_audit(logs: list[EventDict]) -> EventDict:
    entries = [log for log in logs if log["event"] == _AUDIT_EVENT]
    assert len(entries) == 1
    return entries[0]
