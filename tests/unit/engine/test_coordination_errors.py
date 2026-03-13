"""Tests for coordination error classes."""

import pytest

from ai_company.engine.errors import (
    CoordinationError,
    CoordinationPhaseError,
    EngineError,
)


class TestCoordinationErrors:
    """Coordination error hierarchy tests."""

    @pytest.mark.unit
    def test_coordination_error_is_engine_error(self) -> None:
        """CoordinationError inherits from EngineError."""
        err = CoordinationError("test")
        assert isinstance(err, EngineError)

    @pytest.mark.unit
    def test_coordination_phase_error_is_coordination_error(self) -> None:
        """CoordinationPhaseError inherits from CoordinationError."""
        err = CoordinationPhaseError("failed", phase="decompose")
        assert isinstance(err, CoordinationError)
        assert isinstance(err, EngineError)

    @pytest.mark.unit
    def test_phase_error_carries_phase(self) -> None:
        """CoordinationPhaseError stores the failing phase name."""
        err = CoordinationPhaseError("route failed", phase="route")
        assert err.phase == "route"
        assert str(err) == "route failed"

    @pytest.mark.unit
    def test_phase_error_carries_partial_phases(self) -> None:
        """CoordinationPhaseError stores partial phases."""
        from ai_company.engine.coordination.models import CoordinationPhaseResult

        partial = (
            CoordinationPhaseResult(
                phase="decompose", success=True, duration_seconds=0.1
            ),
            CoordinationPhaseResult(phase="route", success=True, duration_seconds=0.2),
        )
        err = CoordinationPhaseError(
            "execute failed",
            phase="execute",
            partial_phases=partial,
        )
        assert err.partial_phases == partial
        assert len(err.partial_phases) == 2

    @pytest.mark.unit
    def test_phase_error_default_partial_phases(self) -> None:
        """CoordinationPhaseError defaults to empty partial_phases."""
        err = CoordinationPhaseError("failed", phase="decompose")
        assert err.partial_phases == ()
