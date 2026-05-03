"""Boundary tests for the typed settings security export/import.

Phase 2 of RFC #1711. ``import_security_config`` validates the
incoming dict through :func:`synthorg.api.boundary.parse_typed` so
malformed payloads emit ``api.boundary.validation_failed`` and the
controller surfaces a 422 via the existing
:class:`~synthorg.api.errors.DomainValidationError` translation.
"""

import pytest
import structlog
from pydantic import ValidationError

from synthorg.api.boundary import parse_typed
from synthorg.security.config import SecurityConfig


@pytest.mark.unit
class TestSettingsSecurityImportBoundary:
    """Direct coverage of the parse_typed call at the import surface."""

    def test_round_trip_export_then_import(self) -> None:
        original = SecurityConfig()
        dumped = original.model_dump(mode="json")
        validated = parse_typed("settings.security", dumped, SecurityConfig)
        assert validated == original

    def test_out_of_range_int_rejected(self) -> None:
        # ``audit_retention_days`` is bounded ge=0, le=36_500.
        dumped = SecurityConfig().model_dump(mode="json")
        dumped["audit_retention_days"] = -1
        with pytest.raises(ValidationError):
            parse_typed("settings.security", dumped, SecurityConfig)

    def test_invalid_field_value_rejected(self) -> None:
        # ``enforcement_mode`` is an enum; an unknown value is rejected.
        dumped = SecurityConfig().model_dump(mode="json")
        dumped["enforcement_mode"] = "not-a-real-mode"
        with pytest.raises(ValidationError):
            parse_typed("settings.security", dumped, SecurityConfig)

    def test_validation_failure_emits_boundary_log(self) -> None:
        dumped = SecurityConfig().model_dump(mode="json")
        dumped["audit_retention_days"] = -42
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ValidationError),
        ):
            parse_typed("settings.security", dumped, SecurityConfig)
        boundary_logs = [
            log for log in logs if log.get("event") == "api.boundary.validation_failed"
        ]
        assert len(boundary_logs) == 1
        record = boundary_logs[0]
        assert record["boundary"] == "settings.security"
        assert record["log_level"] == "warning"
        assert record["error_type"] == "ValidationError"

    def test_empty_dict_uses_model_defaults(self) -> None:
        # SecurityConfig has all-default fields, so an empty dict
        # must round-trip to a default instance.
        validated = parse_typed("settings.security", {}, SecurityConfig)
        assert validated == SecurityConfig()

    def test_none_input_treated_as_empty_dict(self) -> None:
        validated = parse_typed("settings.security", None, SecurityConfig)
        assert validated == SecurityConfig()
