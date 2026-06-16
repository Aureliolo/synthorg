"""Unit tests for the 7 new Prometheus metric families.

Covers provider token/cost counters, API request histogram, task
counters/histogram, tool counters/histogram, audit chain counters,
and OTLP export health counters. Cardinality guards raise
``ValueError`` on invalid label values so bad callers fail loud
instead of silently polluting the metric family.
"""

import pytest
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

from synthorg.observability.prometheus_collector import PrometheusCollector
from synthorg.observability.prometheus_labels import (
    _LabelSnapshot,
    status_class,
    update_label_snapshot,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _seed_tool_name_snapshot() -> None:
    """Seed the prometheus label snapshot per test.

    ``record_tool_invocation`` validates ``tool_name`` and
    ``record_provider_usage`` / ``record_provider_error`` fold the
    ``provider`` label against the snapshot maintained by
    ``PrometheusCollector.refresh()``; these unit tests never invoke
    ``refresh`` so we seed both sets manually. The
    top-level ``tests/conftest.py`` autouse fixture resets the
    snapshot before AND after every test, so seeding here is
    function-scoped (a session-scoped seed would be wiped
    immediately) and a per-test teardown reset is redundant -- the
    top-level fixture already handles cleanup, so this fixture
    cannot leave a populated snapshot leaking into unrelated files.
    """
    update_label_snapshot(
        _LabelSnapshot(
            tool_names=frozenset({"web_search", "calculator", "t"}),
            tool_names_seeded=True,
            providers=frozenset({"example-provider"}),
            providers_seeded=True,
        ),
    )


def _parse(
    collector: PrometheusCollector,
) -> dict[str, list[tuple[dict[str, str], float]]]:
    """Scrape and parse into ``{name: [(labels, value), ...]}``.

    Filters out counter ``_created`` samples (prometheus_client emits
    both ``_total`` and ``_created`` per labelset): without the filter,
    callers using ``next(s for lbl, s in ...)`` could pick up the
    timestamp sample instead of the counter, silently breaking
    assertions. Histogram ``_bucket`` samples are also skipped; tests
    that care about buckets inspect the raw scrape text directly.
    """
    text = generate_latest(collector.registry).decode("utf-8")
    out: dict[str, list[tuple[dict[str, str], float]]] = {}
    for family in text_string_to_metric_families(text):
        out.setdefault(family.name, [])
        for sample in family.samples:
            if sample.name.endswith(("_created", "_bucket")):
                continue
            out[family.name].append((dict(sample.labels), sample.value))
    return out


# -- Provider metrics --------------------------------------------------------


def test_record_provider_usage_updates_tokens_and_cost() -> None:
    collector = PrometheusCollector()
    collector.record_provider_usage(
        provider="example-provider",
        model="large",
        input_tokens=100,
        output_tokens=50,
        cost=0.0125,
    )
    parsed = _parse(collector)
    tokens = parsed["synthorg_provider_tokens"]
    input_row = next(s for lbl, s in tokens if lbl.get("direction") == "input")
    output_row = next(s for lbl, s in tokens if lbl.get("direction") == "output")
    assert input_row == 100.0
    assert output_row == 50.0
    cost = parsed["synthorg_provider_cost"]
    (cost_labels, cost_value), *_ = cost
    assert cost_labels == {"provider": "example-provider", "model": "large"}
    assert cost_value == pytest.approx(0.0125)


def test_record_provider_usage_rejects_negative_values() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="non-negative"):
        collector.record_provider_usage(
            provider="p",
            model="m",
            input_tokens=-1,
            output_tokens=0,
            cost=0.0,
        )


# -- API request metrics -----------------------------------------------------


def test_record_api_request_bucketed_by_status_class() -> None:
    collector = PrometheusCollector()
    collector.record_api_request(
        method="GET",
        route="/agents/{agent_id}",
        status_code=200,
        duration_sec=0.042,
    )
    collector.record_api_request(
        method="POST",
        route="/tasks",
        status_code=503,
        duration_sec=0.5,
    )
    text = generate_latest(collector.registry).decode("utf-8")
    # Histogram count samples are emitted as ``<name>_count{...}`` lines.
    assert (
        'synthorg_api_request_duration_seconds_count{method="GET",'
        'route="/agents/{agent_id}",status_class="2xx"} 1.0' in text
    )
    assert (
        'synthorg_api_request_duration_seconds_count{method="POST",'
        'route="/tasks",status_class="5xx"} 1.0' in text
    )


def test_record_api_request_rejects_invalid_status_code() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="invalid status_code"):
        collector.record_api_request(
            method="GET",
            route="/x",
            status_code=999,
            duration_sec=0.1,
        )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (100, "1xx"),
        (200, "2xx"),
        (399, "3xx"),
        (404, "4xx"),
        (599, "5xx"),
        (99, "invalid"),
        (600, "invalid"),
    ],
)
def test_status_class_boundaries(code: int, expected: str) -> None:
    assert status_class(code) == expected


# -- Task metrics ------------------------------------------------------------


def test_record_task_run_counts_and_observes() -> None:
    collector = PrometheusCollector()
    collector.record_task_run(outcome="succeeded", duration_sec=2.5)
    collector.record_task_run(outcome="failed", duration_sec=10.0)
    parsed = _parse(collector)
    by_outcome = {
        labels["outcome"]: value for labels, value in parsed["synthorg_task_runs"]
    }
    assert by_outcome["succeeded"] == 1.0
    assert by_outcome["failed"] == 1.0


def test_record_task_run_rejects_unknown_outcome() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown task outcome"):
        collector.record_task_run(outcome="weird", duration_sec=1.0)


# -- Tool metrics ------------------------------------------------------------


def test_record_tool_invocation_increments_by_outcome() -> None:
    collector = PrometheusCollector()
    collector.record_tool_invocation(
        tool_name="web_search", outcome="success", duration_sec=0.3
    )
    collector.record_tool_invocation(
        tool_name="web_search", outcome="error", duration_sec=0.1
    )
    parsed = _parse(collector)
    by_key = {
        (labels["tool_name"], labels["outcome"]): value
        for labels, value in parsed["synthorg_tool_invocations"]
    }
    assert by_key[("web_search", "success")] == 1.0
    assert by_key[("web_search", "error")] == 1.0


def test_record_tool_invocation_rejects_unknown_outcome() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown tool outcome"):
        collector.record_tool_invocation(
            tool_name="t", outcome="bogus", duration_sec=1.0
        )


# -- Audit chain metrics -----------------------------------------------------


def test_record_audit_append_updates_depth_and_timestamp() -> None:
    collector = PrometheusCollector()
    collector.record_audit_append(
        status="signed", chain_depth=42, timestamp_unix=1_700_000_000.0
    )
    parsed = _parse(collector)
    appends = {
        labels["status"]: value
        for labels, value in parsed["synthorg_audit_chain_appends"]
    }
    assert appends["signed"] == 1.0
    depth = next(v for _, v in parsed["synthorg_audit_chain_depth"])
    assert depth == 42.0
    ts = next(
        v for _, v in parsed["synthorg_audit_chain_last_append_timestamp_seconds"]
    )
    assert ts == 1_700_000_000.0


def test_record_audit_append_rejects_unknown_status() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown audit append status"):
        collector.record_audit_append(
            status="unknown", chain_depth=1, timestamp_unix=0.0
        )


# -- OTLP export health ------------------------------------------------------


def test_record_otlp_export_increments_outcome_counter() -> None:
    collector = PrometheusCollector()
    collector.record_otlp_export(kind="logs", outcome="success")
    collector.record_otlp_export(kind="traces", outcome="failure", dropped_records=3)
    parsed = _parse(collector)
    batches = {
        (labels["kind"], labels["outcome"]): value
        for labels, value in parsed["synthorg_otlp_export_batches"]
    }
    assert batches[("logs", "success")] == 1.0
    assert batches[("traces", "failure")] == 1.0
    dropped = {
        labels["kind"]: value
        for labels, value in parsed["synthorg_otlp_export_dropped_records"]
    }
    assert dropped["traces"] == 3.0


def test_record_otlp_export_rejects_unknown_kind() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown OTLP kind"):
        collector.record_otlp_export(kind="metrics", outcome="success")


def test_record_log_sink_export_increments_outcome_counter() -> None:
    collector = PrometheusCollector()
    collector.record_log_sink_export(sink="http", outcome="success")
    collector.record_log_sink_export(sink="syslog", outcome="failure")
    parsed = _parse(collector)
    events = {
        (labels["sink"], labels["outcome"]): value
        for labels, value in parsed["synthorg_log_sink_events"]
    }
    assert events[("http", "success")] == 1.0
    assert events[("syslog", "failure")] == 1.0


def test_record_log_sink_export_rejects_unknown_sink() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown log sink"):
        collector.record_log_sink_export(sink="kafka", outcome="success")


# -- Metrics endpoint families present --------------------------------------


def test_all_new_metric_families_registered() -> None:
    """Scraping a fresh collector exposes all new metric names."""
    collector = PrometheusCollector()
    text = generate_latest(collector.registry).decode("utf-8")
    for name in (
        "synthorg_provider_tokens_total",
        "synthorg_provider_cost_total",
        "synthorg_api_request_duration_seconds",
        "synthorg_task_runs_total",
        "synthorg_task_duration_seconds",
        "synthorg_tool_invocations_total",
        "synthorg_tool_duration_seconds",
        "synthorg_audit_chain_appends_total",
        "synthorg_audit_chain_depth",
        "synthorg_audit_chain_last_append_timestamp_seconds",
        "synthorg_otlp_export_batches_total",
        "synthorg_otlp_export_dropped_records_total",
        "synthorg_provider_errors_total",
        "synthorg_cache_operations_total",
        "synthorg_api_error_classification_total",
    ):
        assert f"# HELP {name}" in text, f"{name} not exposed"


# -- Provider error metric ---------------------------------------------------


def test_record_provider_error_increments_by_class() -> None:
    collector = PrometheusCollector()
    collector.record_provider_error(
        provider="example-provider",
        model="large",
        error_class="rate_limit",
    )
    collector.record_provider_error(
        provider="example-provider",
        model="large",
        error_class="rate_limit",
    )
    collector.record_provider_error(
        provider="example-provider",
        model="large",
        error_class="timeout",
    )
    parsed = _parse(collector)
    samples = parsed["synthorg_provider_errors"]
    rate_limit_row = next(
        s for lbl, s in samples if lbl.get("error_class") == "rate_limit"
    )
    timeout_row = next(s for lbl, s in samples if lbl.get("error_class") == "timeout")
    assert rate_limit_row == 2.0
    assert timeout_row == 1.0


def test_record_provider_error_rejects_unknown_class() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="error_class"):
        collector.record_provider_error(
            provider="example-provider",
            model="large",
            error_class="bogus",
        )


# -- Cache operation metric --------------------------------------------------


def test_record_cache_operation_partitions_by_name_and_outcome() -> None:
    collector = PrometheusCollector()
    collector.record_cache_operation(cache_name="mcp_result", outcome="hit")
    collector.record_cache_operation(cache_name="mcp_result", outcome="hit")
    collector.record_cache_operation(cache_name="mcp_result", outcome="miss")
    collector.record_cache_operation(cache_name="reranker", outcome="hit")
    collector.record_cache_operation(cache_name="reranker", outcome="evict")
    parsed = _parse(collector)
    samples = parsed["synthorg_cache_operations"]
    mcp_hit = next(
        s for lbl, s in samples if lbl == {"cache_name": "mcp_result", "outcome": "hit"}
    )
    mcp_miss = next(
        s
        for lbl, s in samples
        if lbl == {"cache_name": "mcp_result", "outcome": "miss"}
    )
    reranker_evict = next(
        s for lbl, s in samples if lbl == {"cache_name": "reranker", "outcome": "evict"}
    )
    assert mcp_hit == 2.0
    assert mcp_miss == 1.0
    assert reranker_evict == 1.0


def test_record_cache_operation_rejects_unknown_cache() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="cache_name"):
        collector.record_cache_operation(cache_name="bogus-cache", outcome="hit")


def test_record_cache_operation_rejects_unknown_outcome() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="cache outcome"):
        collector.record_cache_operation(cache_name="mcp_result", outcome="done")


# -- API error classification metric -----------------------------------------


def test_record_api_error_partitions_by_category_and_status_class() -> None:
    collector = PrometheusCollector()
    collector.record_api_error(category="validation", status_code=422)
    collector.record_api_error(category="validation", status_code=400)
    collector.record_api_error(category="internal", status_code=503)
    parsed = _parse(collector)
    samples = parsed["synthorg_api_error_classification"]
    val_4xx = next(
        s
        for lbl, s in samples
        if lbl == {"category": "validation", "status_class": "4xx"}
    )
    int_5xx = next(
        s
        for lbl, s in samples
        if lbl == {"category": "internal", "status_class": "5xx"}
    )
    assert val_4xx == 2.0
    assert int_5xx == 1.0


def test_record_api_error_rejects_2xx() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="not 4xx/5xx"):
        collector.record_api_error(category="validation", status_code=200)


def test_record_api_error_rejects_unknown_category() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="api error category"):
        collector.record_api_error(category="bogus", status_code=500)
