"""Regression tests for the telemetry-backend reporter.

The collector's ``_send`` helper only flips the "delivered" return
value to ``False`` when ``report()`` raises. The reporter must
propagate backend exceptions instead of logging-and-swallowing
them: a swallowed exception turns a failed write into a
successful-looking delivery (``*_SENT`` debug events still fire),
which is the bug class these tests lock down. The reporter does
**not** log ``TELEMETRY_REPORT_FAILED`` itself --
:meth:`TelemetryCollector._send` owns that alert and duplicate
logs would double-count failures.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from synthorg.telemetry.protocol import TelemetryEvent

if TYPE_CHECKING:
    # Optional-dependency guard: the logfire extra may be absent at
    # runtime (tests importorskip it per-case), so the reporter type is
    # imported for annotations only.
    from synthorg.telemetry.reporters.logfire import LogfireReporter


def _event() -> TelemetryEvent:
    return TelemetryEvent(
        event_type="deployment.heartbeat",
        deployment_id="00000000-0000-0000-0000-000000000001",
        synthorg_version="test",
        python_version="3.14.0",
        os_platform="Linux",
        environment="test",
        timestamp=datetime.now(UTC),
        properties={},
    )


@pytest.mark.unit
class TestLogfireReporterReportRaises:
    """``report()`` must propagate backend failures, not swallow them."""

    @pytest.fixture
    def reporter(self) -> LogfireReporter:
        pytest.importorskip(
            "logfire",
            reason="logfire extra not installed in this environment",
        )
        import logfire as real_logfire

        from synthorg.telemetry.reporters.logfire import LogfireReporter

        # ``logfire.configure`` is patched so it does NOT spawn the
        # background ``check_logfire_token`` thread that would
        # otherwise hit the real Logfire API with a bogus token
        # and raise an unhandled 401 in a worker thread;
        # pytest-threadexception surfaces those as test errors in
        # the full-suite run the pre-push hook triggers.
        with patch.object(real_logfire, "configure"):
            return LogfireReporter(
                token="pylf_v1_test_000000000000000000000000000000000000000000",
            )

    async def test_backend_exception_propagates(
        self,
        reporter: LogfireReporter,
    ) -> None:
        event = _event()
        with (
            patch.object(
                reporter._logfire,
                "info",
                side_effect=RuntimeError("backend down"),
            ),
            pytest.raises(RuntimeError, match="backend down"),
        ):
            await reporter.report(event)

    async def test_reporter_does_not_emit_report_failed_alert(
        self,
        reporter: LogfireReporter,
    ) -> None:
        """The collector owns ``TELEMETRY_REPORT_FAILED``; reporter stays quiet."""
        event = _event()
        with (
            patch.object(
                reporter._logfire,
                "info",
                side_effect=RuntimeError("backend down"),
            ),
            patch(
                "synthorg.telemetry.reporters.logfire.logger",
            ) as mock_logger,
            pytest.raises(RuntimeError),
        ):
            await reporter.report(event)
        mock_logger.warning.assert_not_called()

    async def test_flush_swallows_ordinary_failure(
        self,
        reporter: LogfireReporter,
    ) -> None:
        """A routine exporter failure degrades to a warning, never raises."""
        with patch.object(
            reporter._logfire,
            "force_flush",
            side_effect=RuntimeError("exporter down"),
        ):
            await reporter.flush()

    async def test_flush_memory_error_propagates(
        self,
        reporter: LogfireReporter,
    ) -> None:
        with (
            patch.object(
                reporter._logfire,
                "force_flush",
                side_effect=MemoryError,
            ),
            pytest.raises(MemoryError),
        ):
            await reporter.flush()

    async def test_shutdown_memory_error_propagates(
        self,
        reporter: LogfireReporter,
    ) -> None:
        with (
            patch.object(reporter._logfire, "force_flush"),
            patch.object(
                reporter._logfire,
                "shutdown",
                side_effect=MemoryError,
            ),
            pytest.raises(MemoryError),
        ):
            await reporter.shutdown()


@pytest.mark.unit
class TestLogfireReporterConfigure:
    """``configure()`` call shape: silences introspection + tags environment."""

    def test_configure_receives_inspect_arguments_false_and_environment(
        self,
    ) -> None:
        """``configure()`` silences the introspection warning and tags env.

        The ``assert_called_once_with`` form locks the full kwarg
        set: an accidental extra kwarg (e.g. a future ``tags=...``
        slip) would break this test instead of sneaking past a
        partial-kwargs check.
        """
        pytest.importorskip(
            "logfire",
            reason="logfire extra not installed in this environment",
        )
        import logfire as real_logfire

        from synthorg.telemetry.reporters.logfire import LogfireReporter

        with patch.object(real_logfire, "configure") as mock_configure:
            LogfireReporter(
                token="pylf_v1_test_000000000000000000000000000000000000000000",
                environment="pre-release",
            )

        mock_configure.assert_called_once()
        kwargs = mock_configure.call_args.kwargs
        expected_keys = {
            "token",
            "send_to_logfire",
            "service_name",
            "service_version",
            "environment",
            "inspect_arguments",
        }
        assert set(kwargs) == expected_keys, (
            f"configure() kwarg drift: got {set(kwargs)}, want {expected_keys}"
        )
        assert kwargs["inspect_arguments"] is False
        assert kwargs["environment"] == "pre-release"
        assert kwargs["service_name"] == "synthorg-telemetry"
        assert kwargs["send_to_logfire"] == "if-token-present"

    async def test_report_includes_environment_kwarg(self) -> None:
        """Per-record ``environment`` kwarg is attached to every ``info()`` call."""
        pytest.importorskip(
            "logfire",
            reason="logfire extra not installed in this environment",
        )
        import logfire as real_logfire

        from synthorg.telemetry.reporters.logfire import LogfireReporter

        with patch.object(real_logfire, "configure"):
            reporter = LogfireReporter(
                token="pylf_v1_test_000000000000000000000000000000000000000000",
                environment="ci",
            )

        event = TelemetryEvent(
            event_type="deployment.heartbeat",
            deployment_id="00000000-0000-0000-0000-000000000002",
            synthorg_version="test",
            python_version="3.14.0",
            os_platform="Linux",
            environment="ci",
            timestamp=datetime.now(UTC),
            properties={},
        )
        with patch.object(reporter._logfire, "info") as mock_info:
            await reporter.report(event)

        mock_info.assert_called_once()
        kwargs = mock_info.call_args.kwargs
        assert kwargs["environment"] == "ci"
        assert kwargs["deployment_id"] == "00000000-0000-0000-0000-000000000002"

    async def test_reserved_kwargs_in_properties_are_filtered(self) -> None:
        """Reserved kwarg names in ``properties`` are dropped before unpack.

        Belt-and-suspenders defense against a future scrubber
        allowlist admitting a reserved name (``environment``,
        ``deployment_id``, etc.) which would otherwise collide with
        the explicit kwargs below and raise ``TypeError``. The
        property value from the event is discarded; the explicit
        kwarg (sourced from the event's top-level field) is the one
        that lands in Logfire.
        """
        pytest.importorskip(
            "logfire",
            reason="logfire extra not installed in this environment",
        )
        import logfire as real_logfire

        from synthorg.telemetry.reporters.logfire import LogfireReporter

        with patch.object(real_logfire, "configure"):
            reporter = LogfireReporter(
                token="pylf_v1_test_000000000000000000000000000000000000000000",
                environment="prod",
            )

        event = TelemetryEvent(
            event_type="deployment.heartbeat",
            deployment_id="00000000-0000-0000-0000-000000000003",
            synthorg_version="test",
            python_version="3.14.0",
            os_platform="Linux",
            environment="prod",
            timestamp=datetime.now(UTC),
            properties={
                "environment": "SMUGGLED",
                "deployment_id": "SMUGGLED",
                "event_timestamp": "SMUGGLED",
                "synthorg_version": "SMUGGLED",
                "python_version": "SMUGGLED",
                "os_platform": "SMUGGLED",
                "allowed_custom_key": "kept",
            },
        )
        with patch.object(reporter._logfire, "info") as mock_info:
            await reporter.report(event)

        mock_info.assert_called_once()
        kwargs = mock_info.call_args.kwargs
        # Explicit kwargs win; the smuggled property values are
        # dropped before ``**safe_properties`` unpack. No
        # ``TypeError`` raised from duplicate kwargs.
        assert kwargs["environment"] == "prod"
        assert kwargs["deployment_id"] == "00000000-0000-0000-0000-000000000003"
        assert kwargs["synthorg_version"] == "test"
        assert kwargs["python_version"] == "3.14.0"
        assert kwargs["os_platform"] == "Linux"
        assert "SMUGGLED" not in kwargs.values()
        # Non-reserved keys still pass through unchanged.
        assert kwargs["allowed_custom_key"] == "kept"
