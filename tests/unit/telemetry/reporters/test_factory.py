"""Tests for the telemetry reporter factory."""

from unittest.mock import patch

import pytest
import structlog.testing

from synthorg.telemetry.config import TelemetryBackend, TelemetryConfig
from synthorg.telemetry.reporters import create_reporter
from synthorg.telemetry.reporters.errors import (
    LogfireConfigureError,
)
from synthorg.telemetry.reporters.noop import NoopReporter


@pytest.mark.unit
class TestCreateReporter:
    """Reporter factory tests."""

    def test_disabled_returns_noop(self) -> None:
        config = TelemetryConfig(enabled=False)
        reporter = create_reporter(config)
        assert isinstance(reporter, NoopReporter)

    def test_noop_backend_returns_noop(self) -> None:
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        reporter = create_reporter(config)
        assert isinstance(reporter, NoopReporter)

    def test_sentinel_token_falls_back_to_noop_with_log(self) -> None:
        """Build artifact missing the embedded token -> NoopReporter + WARNING."""
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.LOGFIRE)
        with (
            patch(
                "synthorg.telemetry.reporters.is_token_embedded",
                return_value=False,
            ),
            structlog.testing.capture_logs() as logs,
        ):
            reporter = create_reporter(config)
        assert isinstance(reporter, NoopReporter)
        warnings = [
            log
            for log in logs
            if log.get("event") == "telemetry.report.failed"
            and log.get("detail") == "logfire_token_missing"
        ]
        assert len(warnings) == 1
        assert warnings[0]["error_type"] == "LogfireTokenMissingError"

    def test_import_failure_falls_back_to_noop_with_real_error_type(self) -> None:
        """Logfire import failure logs the actual error_type, not a hardcoded string."""
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.LOGFIRE)

        def _raise_import_error(**_kwargs: object) -> None:
            msg = "no logfire here"
            raise ImportError(msg)

        with (
            patch(
                "synthorg.telemetry.reporters.is_token_embedded",
                return_value=True,
            ),
            patch(
                "synthorg.telemetry.reporters.logfire.LogfireReporter",
                side_effect=_raise_import_error,
            ),
            structlog.testing.capture_logs() as logs,
        ):
            reporter = create_reporter(config)
        assert isinstance(reporter, NoopReporter)
        warnings = [
            log
            for log in logs
            if log.get("event") == "telemetry.report.failed"
            and log.get("detail") == "logfire_import_failed"
        ]
        assert len(warnings) == 1
        assert warnings[0]["error_type"] == "ImportError"

    def test_configure_failure_falls_back_to_noop(self) -> None:
        """LogfireConfigureError -> NoopReporter + WARNING with real error_type."""
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.LOGFIRE)

        def _raise_configure_error(**_kwargs: object) -> None:
            msg = "configure rejected"
            raise LogfireConfigureError(msg)

        with (
            patch(
                "synthorg.telemetry.reporters.is_token_embedded",
                return_value=True,
            ),
            patch(
                "synthorg.telemetry.reporters.logfire.LogfireReporter",
                side_effect=_raise_configure_error,
            ),
            structlog.testing.capture_logs() as logs,
        ):
            reporter = create_reporter(config)
        assert isinstance(reporter, NoopReporter)
        warnings = [
            log
            for log in logs
            if log.get("event") == "telemetry.report.failed"
            and log.get("detail") == "logfire_configure_failed"
        ]
        assert len(warnings) == 1
        assert warnings[0]["error_type"] == "LogfireConfigureError"

    def test_unknown_exception_propagates(self) -> None:
        """Unknown exception classes must NOT be silently swallowed."""
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.LOGFIRE)

        class _CustomError(Exception):
            pass

        def _raise_custom(**_kwargs: object) -> None:
            msg = "this is not a sanctioned failure mode"
            raise _CustomError(msg)

        with (
            patch(
                "synthorg.telemetry.reporters.is_token_embedded",
                return_value=True,
            ),
            patch(
                "synthorg.telemetry.reporters.logfire.LogfireReporter",
                side_effect=_raise_custom,
            ),
            pytest.raises(_CustomError),
        ):
            create_reporter(config)

    def test_logfire_backend_returns_reporter_or_noop(self) -> None:
        """Real init succeeds OR fails through one of the three sanctioned paths."""
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.LOGFIRE)
        reporter = create_reporter(config)
        reporter_name = type(reporter).__name__
        assert reporter_name in {"LogfireReporter", "NoopReporter"}

    def test_config_environment_threaded_into_logfire_reporter(self) -> None:
        """``config.environment`` must reach ``LogfireReporter.__init__``."""
        pytest.importorskip(
            "logfire",
            reason="logfire extra not installed in this environment",
        )

        config = TelemetryConfig(
            enabled=True,
            backend=TelemetryBackend.LOGFIRE,
            environment="staging",
        )

        with (
            patch(
                "synthorg.telemetry.reporters.is_token_embedded",
                return_value=True,
            ),
            patch(
                "synthorg.telemetry.reporters.EMBEDDED_LOGFIRE_TOKEN",
                "pylf_v1_test_000000000000000000000000000000000000000000",
            ),
            patch(
                "synthorg.telemetry.reporters.logfire.LogfireReporter",
            ) as mock_reporter_cls,
        ):
            create_reporter(config)

        mock_reporter_cls.assert_called_once_with(
            token="pylf_v1_test_000000000000000000000000000000000000000000",
            environment="staging",
        )
