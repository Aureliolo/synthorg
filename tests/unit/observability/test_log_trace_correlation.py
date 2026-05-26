"""Error-path coverage for the trace-correlation log processor."""

from unittest.mock import patch

import pytest

from synthorg.observability.log_trace_correlation import inject_trace_context


@pytest.mark.unit
class TestInjectTraceContextErrorPaths:
    def test_span_lookup_failure_fails_open(self) -> None:
        event_dict = {"event": "something happened"}

        # A non-critical OTel failure during the current-span lookup
        # must not drop the log record: the processor returns the dict
        # unchanged (best-effort enrichment, fail open).
        with patch(
            "opentelemetry.trace.get_current_span",
            side_effect=RuntimeError("otel boom"),
        ):
            result = inject_trace_context(None, "info", event_dict)

        assert result is event_dict
        assert "trace_id" not in result
