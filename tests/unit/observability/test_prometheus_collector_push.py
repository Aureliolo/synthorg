"""Tests for the new HYG-1 push-updated Prometheus record_* methods.

Covers ``record_escalation_queue_depth``, ``record_agent_identity_change``,
and ``record_workflow_execution`` -- including the WARNING log paths,
label-value validation, and cardinality guards that matter for
production dashboards.
"""

from collections.abc import Iterator

import pytest
import structlog

from synthorg.observability.events.metrics import METRICS_SCRAPE_FAILED
from synthorg.observability.prometheus_collector import PrometheusCollector
from synthorg.observability.prometheus_labels import (
    _LabelSnapshot,
    _reset_label_snapshot_for_tests,
    update_label_snapshot,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _seed_label_snapshot() -> Iterator[None]:
    """Seed the registry-bound snapshot for the labels exercised below.

    The validators in ``prometheus_labels`` fail closed on every
    state (no bootstrap pass-through), so the test fixtures must
    seed every ``agent_id`` / ``workflow_definition_id`` /
    ``department`` value the tests record. This fixture covers all
    of them for the file in one place.
    """
    update_label_snapshot(
        _LabelSnapshot(
            agent_ids=frozenset(
                {
                    "agent-1",
                    "agent-2",
                    "agent-created",
                    "agent-updated",
                    "agent-rolled_back",
                    "agent-archived",
                },
            ),
            workflow_definition_ids=frozenset(
                {"wf_onboarding_v1", "wf_a", "wf_long"},
            ),
            departments=frozenset({"sales"}),
            agent_ids_seeded=True,
            workflow_definition_ids_seeded=True,
            departments_seeded=True,
        ),
    )
    yield
    _reset_label_snapshot_for_tests()


@pytest.fixture
def collector() -> PrometheusCollector:
    """Build a collector with a unique prefix so tests don't collide.

    ``prometheus_client`` raises if the same metric name is registered
    into the same registry twice; each collector here owns its own
    ``CollectorRegistry`` so parallel runs are safe.
    """
    return PrometheusCollector(prefix="synthorg_test")


def _sample_value(
    collector: PrometheusCollector,
    metric_name: str,
    labels: dict[str, str],
) -> float:
    """Read a labelled metric value out of the collector's registry."""
    for metric in collector.registry.collect():
        for sample in metric.samples:
            if sample.name == metric_name and sample.labels == labels:
                return float(sample.value)
    return 0.0


class TestRecordEscalationQueueDepth:
    def test_happy_path_sets_gauge(
        self,
        collector: PrometheusCollector,
    ) -> None:
        collector.record_escalation_queue_depth(department="sales", depth=7)
        value = _sample_value(
            collector,
            "synthorg_test_escalation_queue_depth",
            {"department": "sales"},
        )
        assert value == 7.0

    def test_empty_department_raises_and_logs(
        self,
        collector: PrometheusCollector,
    ) -> None:
        # The WARNING should fire via METRICS_SCRAPE_FAILED so dashboards
        # surface validation failures the same way scrape errors appear.
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(ValueError, match="department must be non-empty"),
        ):
            collector.record_escalation_queue_depth(department="", depth=3)
        assert any(rec.get("event") == METRICS_SCRAPE_FAILED for rec in cap)

    def test_negative_depth_rejected(
        self,
        collector: PrometheusCollector,
    ) -> None:
        with pytest.raises(ValueError, match="depth"):
            collector.record_escalation_queue_depth(department="sales", depth=-1)


class TestRecordAgentIdentityChange:
    def test_happy_path_increments_counter(
        self,
        collector: PrometheusCollector,
    ) -> None:
        collector.record_agent_identity_change(
            agent_id="agent-1",
            change_type="created",
        )
        collector.record_agent_identity_change(
            agent_id="agent-1",
            change_type="created",
        )
        value = _sample_value(
            collector,
            "synthorg_test_agent_identity_version_changes_total",
            {"agent_id": "agent-1", "change_type": "created"},
        )
        assert value == 2.0

    def test_all_valid_change_types_accepted(
        self,
        collector: PrometheusCollector,
    ) -> None:
        # The docstring lists created/updated/rolled_back/archived; every
        # one of them must round-trip without raising.
        for change in ("created", "updated", "rolled_back", "archived"):
            collector.record_agent_identity_change(
                agent_id=f"agent-{change}",
                change_type=change,
            )

    def test_unknown_change_type_rejected(
        self,
        collector: PrometheusCollector,
    ) -> None:
        with pytest.raises(ValueError, match="change_type"):
            collector.record_agent_identity_change(
                agent_id="agent-2",
                change_type="exploded",
            )

    def test_empty_agent_id_raises_and_logs(
        self,
        collector: PrometheusCollector,
    ) -> None:
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(ValueError, match="agent_id must be non-empty"),
        ):
            collector.record_agent_identity_change(
                agent_id="",
                change_type="created",
            )
        assert any(rec.get("event") == METRICS_SCRAPE_FAILED for rec in cap)


class TestRecordWorkflowExecution:
    def test_happy_path_observes_histogram(
        self,
        collector: PrometheusCollector,
    ) -> None:
        collector.record_workflow_execution(
            workflow_definition_id="wf_onboarding_v1",
            status="completed",
            duration_seconds=12.5,
        )
        # Histograms expose ``*_count`` + ``*_sum`` samples; assert
        # both moved forward after the single observation.
        count = _sample_value(
            collector,
            "synthorg_test_workflow_execution_seconds_count",
            {"workflow_definition_id": "wf_onboarding_v1", "status": "completed"},
        )
        total = _sample_value(
            collector,
            "synthorg_test_workflow_execution_seconds_sum",
            {"workflow_definition_id": "wf_onboarding_v1", "status": "completed"},
        )
        assert count == 1.0
        assert total == pytest.approx(12.5)

    def test_empty_definition_id_raises_and_logs(
        self,
        collector: PrometheusCollector,
    ) -> None:
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(
                ValueError,
                match="workflow_definition_id must be non-empty",
            ),
        ):
            collector.record_workflow_execution(
                workflow_definition_id="",
                status="completed",
                duration_seconds=1.0,
            )
        assert any(rec.get("event") == METRICS_SCRAPE_FAILED for rec in cap)

    def test_unknown_status_rejected(
        self,
        collector: PrometheusCollector,
    ) -> None:
        with pytest.raises(ValueError, match="status"):
            collector.record_workflow_execution(
                workflow_definition_id="wf_a",
                status="turned_into_a_pumpkin",
                duration_seconds=1.0,
            )

    def test_negative_duration_rejected(
        self,
        collector: PrometheusCollector,
    ) -> None:
        with pytest.raises(ValueError, match="duration_seconds"):
            collector.record_workflow_execution(
                workflow_definition_id="wf_a",
                status="completed",
                duration_seconds=-0.1,
            )


class TestWorkflowHistogramBuckets:
    """The histogram ships explicit buckets so p95/p99 stay meaningful."""

    def test_long_running_buckets_present(
        self,
        collector: PrometheusCollector,
    ) -> None:
        # Observe a 15-minute (900s) execution and verify it lands in
        # a bucket that is actually named in the configured buckets
        # (not rolled up into the ``+Inf`` overflow, which is what
        # Prometheus defaults would have produced).
        collector.record_workflow_execution(
            workflow_definition_id="wf_long",
            status="completed",
            duration_seconds=900.0,
        )
        # The 1800.0 bucket count must be at least 1 -- confirms the
        # explicit buckets include a minutes-to-hours upper range.
        bucket_value = _sample_value(
            collector,
            "synthorg_test_workflow_execution_seconds_bucket",
            {
                "workflow_definition_id": "wf_long",
                "status": "completed",
                "le": "1800.0",
            },
        )
        assert bucket_value >= 1.0
