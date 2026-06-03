"""Regression: silent except sites log error_type + scrubbed error fields.

Six controller sites previously logged a bare warning with no
``error_type`` / ``error`` payload, so an operator triaging a failed
budget query or registry lookup could not see which exception class
fired. Each site now binds ``as exc`` and forwards the scrubbed
``safe_error_description(exc)`` per the prompt-safety redaction rule.

This test patches one representative site (``activities``) to force
the exception path and asserts the warning record carries both
structured fields; the same pattern applies to setup, analytics,
_department_health, and approvals.
"""

from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs
from typeguard import suppress_type_checks

from synthorg.api.controllers import activities
from synthorg.budget.currency import DEFAULT_CURRENCY
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


class _ExplodingResolver:
    """Stand-in ``ConfigResolver`` that raises on ``get_budget_config``."""

    async def get_budget_config(self) -> Any:
        msg = "synthetic budget config outage"
        raise RuntimeError(msg)


@pytest.fixture(autouse=True)
def _structlog_capture_setup() -> None:
    """structlog logger needs at least one bound processor for capture_logs."""
    structlog.reset_defaults()


class TestSilentExceptStructuredLogging:
    async def test_activities_budget_config_failure_logs_error_fields(self) -> None:
        app_state = make_app_state(config_resolver=_ExplodingResolver())
        degraded: list[str] = []
        with capture_logs() as caplog, suppress_type_checks():
            result = await activities._resolve_currency(
                app_state,
                degraded,
            )
        # Default currency fallback fires.
        assert result == DEFAULT_CURRENCY
        # The warning record carries the new structured fields.
        warnings = [r for r in caplog if r.get("log_level") == "warning"]
        assert warnings, "expected a WARNING record from the exception path"
        last = warnings[-1]
        assert last.get("error_type") == "RuntimeError"
        assert isinstance(last.get("error"), str)
        assert last["error"]
