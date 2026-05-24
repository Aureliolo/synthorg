# mypy: disable-error-code="explicit-any"
"""Regression: ``AuditLog.record`` updates the fill-ratio gauge each append.

The gauge value is exposed as
``synthorg_security_audit_log_fill_ratio`` (no labels). Operators
alert when it approaches 1.0 so they can preserve evidence before the
in-memory deque evicts the oldest entry.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.core.types import NotBlankStr
from synthorg.observability.prometheus_collector import PrometheusCollector
from synthorg.security.audit import AuditLog
from synthorg.security.models import AuditEntry

pytestmark = pytest.mark.unit


def _make_entry(entry_id: str) -> AuditEntry:
    return AuditEntry(
        id=NotBlankStr(entry_id),
        timestamp=datetime(2026, 5, 13, tzinfo=UTC),
        agent_id=NotBlankStr("agent-a"),
        tool_name=NotBlankStr("code_write"),
        tool_category=ToolCategory.CODE_EXECUTION,
        action_type=NotBlankStr("code:write"),
        arguments_hash="a" * 64,
        verdict="allow",
        risk_level=ApprovalRiskLevel.LOW,
        reason="test",
        evaluation_duration_ms=1.0,
    )


def _gauge_value(collector: PrometheusCollector) -> float:
    """Read the current gauge value through the collector's registry."""
    samples = collector.registry.collect()
    for metric in samples:
        if metric.name.endswith("synthorg_security_audit_log_fill_ratio"):
            for sample in metric.samples:
                if sample.name.endswith("synthorg_security_audit_log_fill_ratio"):
                    return float(sample.value)
    msg = "synthorg_security_audit_log_fill_ratio gauge not found in registry"
    raise AssertionError(msg)


class TestAuditFillRatioGauge:
    def test_fill_ratio_grows_with_records(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import synthorg.observability.metrics_hub as _hub

        collector = PrometheusCollector(prefix="synthorg")
        monkeypatch.setattr(_hub, "_active", lambda: collector)

        log = AuditLog(max_entries=4)

        log.record(_make_entry("e-1"))
        assert _gauge_value(collector) == pytest.approx(0.25)
        log.record(_make_entry("e-2"))
        assert _gauge_value(collector) == pytest.approx(0.5)
        log.record(_make_entry("e-3"))
        log.record(_make_entry("e-4"))
        assert _gauge_value(collector) == pytest.approx(1.0)
        # Evictions keep the deque at max_entries; ratio stays at 1.0.
        log.record(_make_entry("e-5"))
        assert _gauge_value(collector) == pytest.approx(1.0)

    def test_clear_resets_fill_ratio_to_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``clear()`` must reset the gauge so stale ``1.0`` doesn't linger.

        Without this, an operator alert keyed on the gauge would keep
        firing after a test isolation reset until the next ``record()``
        rewrote the value.
        """
        import synthorg.observability.metrics_hub as _hub

        collector = PrometheusCollector(prefix="synthorg")
        monkeypatch.setattr(_hub, "_active", lambda: collector)

        log = AuditLog(max_entries=4)
        log.record(_make_entry("e-1"))
        log.record(_make_entry("e-2"))
        assert _gauge_value(collector) == pytest.approx(0.5)
        log.clear()
        assert _gauge_value(collector) == pytest.approx(0.0)


# Force pytest-asyncio to register the module (not strictly required here
# since these are sync tests but mirrors the project layout).
__all__: tuple[Any, ...] = ()
