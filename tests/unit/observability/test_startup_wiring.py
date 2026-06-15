"""Tests for observability startup wiring.

Covers the log-shipping sink export-callback wiring and the warning
emitted when no Prometheus collector is present (so an operator missing
the export-outcome series knows wiring was skipped, not idle).
"""

import logging
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
import structlog.testing
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

from synthorg.api.state import AppState
from synthorg.observability.config import SinkConfig
from synthorg.observability.enums import SinkType
from synthorg.observability.events.metrics import METRICS_PROMETHEUS_WIRING_SKIPPED
from synthorg.observability.http_handler import HttpBatchHandler
from synthorg.observability.prometheus_collector import PrometheusCollector
from synthorg.observability.startup_wiring import (
    _wire_prometheus_sinks,
    wire_observability_callbacks,
)
from synthorg.observability.syslog_handler import build_syslog_handler

pytestmark = pytest.mark.unit


def _sink_value(
    collector: PrometheusCollector,
    sink: str,
    outcome: str,
) -> float:
    text = generate_latest(collector.registry).decode("utf-8")
    for family in text_string_to_metric_families(text):
        if family.name != "synthorg_log_sink_events":
            continue
        for sample in family.samples:
            if sample.labels == {"sink": sink, "outcome": outcome}:
                return sample.value
    return 0.0


def test_wire_prometheus_sinks_wires_http_export_callback(
    handler_cleanup: list[logging.Handler],
) -> None:
    """A wired HTTP handler routes its drop outcome into the collector."""
    collector = PrometheusCollector()
    handler = HttpBatchHandler(url="https://logs.example.com", flush_interval=60.0)
    handler_cleanup.append(handler)
    test_logger = logging.getLogger("test.startup_wiring.http")
    test_logger.addHandler(handler)
    try:
        _wire_prometheus_sinks(collector)
        assert handler._export_callback is not None
        # Simulate a failed export batch dropping two records.
        handler._invoke_export_callback("failure", 2)
    finally:
        test_logger.removeHandler(handler)

    assert _sink_value(collector, "http", "failure") == 1.0


def test_wire_prometheus_sinks_wires_syslog_export_callback(
    handler_cleanup: list[logging.Handler],
) -> None:
    """A wired syslog handler routes its drop outcome with sink='syslog'."""
    collector = PrometheusCollector()
    handler = build_syslog_handler(
        SinkConfig(sink_type=SinkType.SYSLOG, syslog_host="localhost"),
        foreign_pre_chain=[],
    )
    handler_cleanup.append(handler)
    test_logger = logging.getLogger("test.startup_wiring.syslog")
    test_logger.addHandler(handler)
    try:
        _wire_prometheus_sinks(collector)
        assert handler._export_callback is not None
        handler._invoke_export_callback("failure", 1)
    finally:
        test_logger.removeHandler(handler)

    assert _sink_value(collector, "syslog", "failure") == 1.0


def test_wire_observability_callbacks_warns_when_collector_absent() -> None:
    """The None-collector branch logs a wiring-skipped warning."""
    slice_stub = SimpleNamespace(
        trace_handler=object(),  # non-None: skips trace-handler build
        prometheus_collector=None,
    )
    app_state = MagicMock(spec=AppState)
    app_state.slice.return_value = slice_stub

    with structlog.testing.capture_logs() as logs:
        wire_observability_callbacks(cast(AppState, app_state))

    assert any(
        rec.get("event") == METRICS_PROMETHEUS_WIRING_SKIPPED
        and rec.get("log_level") == "warning"
        for rec in logs
    ), "absent collector must log METRICS_PROMETHEUS_WIRING_SKIPPED at WARNING"
