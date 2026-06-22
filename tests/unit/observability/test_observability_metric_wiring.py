"""Integration tests for the observability metric emissions.

Pins the acceptance criterion that every wired metric is exercised
end-to-end:

* ``record_client_disconnect`` (validation, log emission, counter
  increment, ValueError on bad transport/reason)
* ``is_known_agent_id`` (non-raising counterpart of
  ``validate_agent_id``)
* ``_rebuild_label_snapshot`` partial-failure fallback (one repo
  raising must NOT blank the whole snapshot)

Direct integration tests for ``record_task_run`` /
``record_workflow_execution`` live next to the engine and workflow
test modules; this module covers the observability-layer
invariants that the engine tests rely on.
"""

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

from synthorg.api.state import AppState
from synthorg.observability.events.metrics import (
    CLIENT_DISCONNECTED,
    METRICS_SCRAPE_FAILED,
)
from synthorg.observability.prometheus_collector import PrometheusCollector
from synthorg.observability.prometheus_labels import (
    _LabelSnapshot,
    _reset_label_snapshot_for_tests,
    _reset_mcp_tool_names_for_tests,
    is_known_agent_id,
    register_mcp_tool_names,
    update_label_snapshot,
)
from synthorg.organization.state import OrganizationStateSlice
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _samples(
    collector: PrometheusCollector,
    name: str,
) -> list[tuple[dict[str, str], float]]:
    """Return ``[(labels, value), ...]`` for the named family."""
    text = generate_latest(collector.registry).decode("utf-8")
    out: list[tuple[dict[str, str], float]] = []
    for family in text_string_to_metric_families(text):
        if family.name != name:
            continue
        for sample in family.samples:
            if sample.name.endswith(("_created", "_bucket")):
                continue
            out.append((dict(sample.labels), sample.value))
    return out


# -- record_client_disconnect ------------------------------------------------


def test_record_client_disconnect_increments_counter_and_logs() -> None:
    collector = PrometheusCollector()
    with structlog.testing.capture_logs() as logs:
        collector.record_client_disconnect(
            transport="websocket",
            reason="transport_error",
        )
    samples = _samples(collector, "synthorg_client_disconnects")
    assert any(
        labels == {"transport": "websocket", "reason": "transport_error"}
        and value == 1.0
        for labels, value in samples
    )
    assert any(
        rec.get("event") == CLIENT_DISCONNECTED
        and rec.get("transport") == "websocket"
        and rec.get("reason") == "transport_error"
        for rec in logs
    )


@pytest.mark.parametrize(
    ("transport", "reason"),
    [
        ("invalid", "client_initiated"),
        ("websocket", "bogus_reason"),
    ],
    ids=["bad_transport", "bad_reason"],
)
def test_record_client_disconnect_rejects_unknown_label(
    transport: str,
    reason: str,
) -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown"):
        collector.record_client_disconnect(transport=transport, reason=reason)


# -- is_known_agent_id helper ------------------------------------------------


def test_is_known_agent_id_rejects_in_bootstrap_mode() -> None:
    # Bootstrap (unseeded) snapshot fails closed -- pre-first-scrape
    # values aren't allowed to leak into the metric labelset.
    assert is_known_agent_id("anything") is False


def test_is_known_agent_id_rejects_after_seed() -> None:
    update_label_snapshot(
        _LabelSnapshot(
            agent_ids=frozenset({"agent-1"}),
            agent_ids_seeded=True,
        ),
    )
    assert is_known_agent_id("agent-1") is True
    assert is_known_agent_id("agent-2") is False


# -- _rebuild_label_snapshot partial-failure ---------------------------------


def _stub_agent(agent_id: str) -> MagicMock:
    agent = MagicMock()
    agent.id = agent_id
    agent.status = "active"
    agent.tools.access_level = "trusted"
    return agent


def _stub_app_state(
    *,
    agents: tuple[MagicMock, ...],
    workflow_repo_raises: bool = False,
    department_service_raises: bool = False,
) -> AppState:
    registry = MagicMock()
    registry.list_active = AsyncMock(return_value=agents)

    backend = MagicMock()
    if workflow_repo_raises:
        backend.workflow_definitions.list_definitions = AsyncMock(
            side_effect=RuntimeError("workflow repo down"),
        )
    else:
        backend.workflow_definitions.list_definitions = AsyncMock(
            return_value=(MagicMock(id="wf-1"),),
        )

    dept_service = MagicMock()
    if department_service_raises:
        dept_service.list_departments = AsyncMock(
            side_effect=RuntimeError("dept service down"),
        )
    else:
        # ``MagicMock(name=...)`` sets the mock's REPR name, not a
        # ``name`` attribute on the returned object, so the
        # collector's ``str(r.name)`` would yield a Mock repr
        # instead of "engineering". ``SimpleNamespace`` gives a
        # real ``name`` attribute that survives the str() coercion.
        dept_service.list_departments = AsyncMock(
            return_value=((SimpleNamespace(name="engineering"),), 1),
        )
    return make_app_state(
        agent_registry=registry,
        persistence=backend,
        slices={OrganizationStateSlice: {"department_service": dept_service}},
    )


async def test_rebuild_label_snapshot_uses_live_registries() -> None:
    collector = PrometheusCollector()
    agents = (_stub_agent("agent-1"), _stub_agent("agent-2"))
    state = _stub_app_state(agents=agents)
    await collector.refresh(state)

    # After refresh the snapshot is seeded; downstream validation
    # rejects unknown ids.
    assert is_known_agent_id("agent-1") is True
    assert is_known_agent_id("agent-99") is False


async def test_rebuild_label_snapshot_partial_when_workflow_repo_fails() -> None:
    collector = PrometheusCollector()
    agents = (_stub_agent("agent-1"),)
    state = _stub_app_state(
        agents=agents,
        workflow_repo_raises=True,
    )
    with structlog.testing.capture_logs() as logs:
        await collector.refresh(state)

    # Agent ids are still seeded even though the workflow repo blew
    # up; the failure is logged with the right component label.
    assert is_known_agent_id("agent-1") is True
    assert any(
        rec.get("event") == METRICS_SCRAPE_FAILED
        and rec.get("component") == "workflow_definition_repo"
        for rec in logs
    )


async def test_rebuild_label_snapshot_partial_when_department_service_fails() -> None:
    collector = PrometheusCollector()
    agents = (_stub_agent("agent-1"),)
    state = _stub_app_state(
        agents=agents,
        department_service_raises=True,
    )
    with structlog.testing.capture_logs() as logs:
        await collector.refresh(state)

    assert is_known_agent_id("agent-1") is True
    assert any(
        rec.get("event") == METRICS_SCRAPE_FAILED
        and rec.get("component") == "department_service"
        for rec in logs
    )


# -- record_approval_decision ------------------------------------------------


def test_record_approval_decision_increments_counter() -> None:
    collector = PrometheusCollector()
    collector.record_approval_decision(outcome="approved")
    samples = _samples(collector, "synthorg_approval_decisions")
    assert any(
        labels == {"outcome": "approved"} and value == 1.0 for labels, value in samples
    )


def test_record_approval_decision_rejects_unknown_outcome() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown"):
        collector.record_approval_decision(outcome="bogus")


# -- record_escalation_outcome -----------------------------------------------


def test_record_escalation_outcome_increments_counter() -> None:
    collector = PrometheusCollector()
    collector.record_escalation_outcome(outcome="resolved")
    samples = _samples(collector, "synthorg_escalation_outcomes")
    assert any(
        labels == {"outcome": "resolved"} and value == 1.0 for labels, value in samples
    )


def test_record_escalation_outcome_rejects_unknown_outcome() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown"):
        collector.record_escalation_outcome(outcome="bogus")


# -- record_blueprint_instantiation ------------------------------------------


def test_record_blueprint_instantiation_increments_counter_and_logs() -> None:
    from synthorg.observability.events.blueprint import BLUEPRINT_INSTANTIATE_OUTCOME

    collector = PrometheusCollector()
    with structlog.testing.capture_logs() as logs:
        collector.record_blueprint_instantiation(
            outcome="success",
            blueprint_name="feature-pipeline",
            duration_sec=0.42,
        )
    samples = _samples(collector, "synthorg_blueprint_instantiations")
    assert any(
        labels == {"outcome": "success"} and value == 1.0 for labels, value in samples
    )
    assert any(
        rec.get("event") == BLUEPRINT_INSTANTIATE_OUTCOME
        and rec.get("outcome") == "success"
        and rec.get("blueprint_name") == "feature-pipeline"
        and rec.get("duration_sec") == 0.42
        for rec in logs
    )


def test_record_blueprint_instantiation_rejects_unknown_outcome() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown"):
        collector.record_blueprint_instantiation(outcome="bogus")


# -- record_settings_mutation ------------------------------------------------


@pytest.mark.parametrize(
    "namespace",
    [
        "security",  # audited namespace
        "budget",  # non-audited namespace; metric must still fire
    ],
)
def test_record_settings_mutation_increments_counter(namespace: str) -> None:
    collector = PrometheusCollector()
    collector.record_settings_mutation(namespace=namespace)
    samples = _samples(collector, "synthorg_settings_mutations")
    assert any(
        labels == {"namespace": namespace} and value == 1.0 for labels, value in samples
    )


def test_record_settings_mutation_rejects_unknown_namespace() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown"):
        collector.record_settings_mutation(namespace="bogus_ns")


# -- record_mcp_handler_outcome ----------------------------------------------


@pytest.fixture
def _seed_mcp_tool_names() -> Iterator[None]:
    """Register the tool names these tests use so the fail-closed
    ``normalize_mcp_tool_label`` emits them verbatim instead of folding
    every label to ``__unknown__`` (bootstrap mode)."""
    register_mcp_tool_names(frozenset({"synthorg_messages_get", "synthorg_tasks_get"}))
    try:
        yield
    finally:
        _reset_mcp_tool_names_for_tests()


@pytest.mark.usefixtures("_seed_mcp_tool_names")
def test_record_mcp_handler_outcome_increments_counter_and_observes_histogram() -> None:
    from synthorg.observability.events.mcp import MCP_HANDLER_OUTCOME

    collector = PrometheusCollector()
    register_mcp_tool_names(frozenset({"synthorg_messages_get"}))
    with structlog.testing.capture_logs() as logs:
        collector.record_mcp_handler_outcome(
            tool="synthorg_messages_get",
            outcome="success",
            duration_sec=0.123,
        )
    counter_samples = _samples(collector, "synthorg_mcp_handler_outcomes")
    assert any(
        labels == {"tool": "synthorg_messages_get", "outcome": "success"}
        and value == 1.0
        for labels, value in counter_samples
    )
    # Histogram emits {family}_count series with same labels; assert presence.
    histogram_samples = _samples(collector, "synthorg_mcp_handler_duration_seconds")
    assert any(
        labels == {"tool": "synthorg_messages_get", "outcome": "success"}
        and value == 1.0
        for labels, value in histogram_samples
        # ``_count`` carries the observation-count; ``_sum`` carries the
        # accumulated time; both have the same labels.
    )
    assert any(
        rec.get("event") == MCP_HANDLER_OUTCOME
        and rec.get("tool") == "synthorg_messages_get"
        and rec.get("outcome") == "success"
        and rec.get("duration_sec") == 0.123
        for rec in logs
    )


def test_record_mcp_handler_outcome_folds_unknown_tool_in_bootstrap() -> None:
    """Regression (audit 132): with no registered tool names (bootstrap),
    a caller-supplied tool label MUST fold to ``__unknown__`` rather than
    reaching Prometheus verbatim and exploding cardinality."""
    _reset_mcp_tool_names_for_tests()
    collector = PrometheusCollector()
    collector.record_mcp_handler_outcome(
        tool="attacker_supplied_" + "x" * 200,
        outcome="success",
        duration_sec=0.01,
    )
    counter_samples = _samples(collector, "synthorg_mcp_handler_outcomes")
    tool_labels = {labels["tool"] for labels, _ in counter_samples}
    assert tool_labels == {"__unknown__"}


def test_record_mcp_handler_outcome_rejects_unknown_outcome() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown"):
        collector.record_mcp_handler_outcome(
            tool="any_tool",
            outcome="bogus",
            duration_sec=0.0,
        )


@pytest.mark.usefixtures("_seed_mcp_tool_names")
@pytest.mark.parametrize(
    "outcome",
    [
        "error",
        "validation_error",
        "guardrail_violated",
        "not_found",
        "capability_unsupported",
    ],
)
def test_record_mcp_handler_outcome_records_all_error_outcomes(outcome: str) -> None:
    """Every bounded MCP error outcome flows through counter + histogram."""
    collector = PrometheusCollector()
    register_mcp_tool_names(frozenset({"synthorg_tasks_get"}))
    collector.record_mcp_handler_outcome(
        tool="synthorg_tasks_get",
        outcome=outcome,
        duration_sec=0.05,
    )
    counter_samples = _samples(collector, "synthorg_mcp_handler_outcomes")
    assert any(
        labels == {"tool": "synthorg_tasks_get", "outcome": outcome} and value == 1.0
        for labels, value in counter_samples
    )
    histogram_samples = _samples(collector, "synthorg_mcp_handler_duration_seconds")
    assert any(
        labels == {"tool": "synthorg_tasks_get", "outcome": outcome} and value == 1.0
        for labels, value in histogram_samples
    )


# -- record_provider_usage / record_provider_error label bounding ------------


@pytest.fixture
def _seed_provider_names() -> Iterator[None]:
    """Seed the snapshot with one known provider so ``normalize_provider_label``
    keeps it verbatim and folds everything else to ``__unknown__``."""
    update_label_snapshot(
        _LabelSnapshot(providers=frozenset({"example-provider"}), providers_seeded=True)
    )
    try:
        yield
    finally:
        _reset_label_snapshot_for_tests()


@pytest.mark.usefixtures("_seed_provider_names")
def test_record_provider_usage_keeps_known_provider_and_model() -> None:
    """Regression (audit 132): a registered provider plus a well-formed model
    id reach Prometheus verbatim."""
    collector = PrometheusCollector()
    collector.record_provider_usage(
        provider="example-provider",
        model="example-large-001",
        input_tokens=10,
        output_tokens=5,
        cost=0.01,
    )
    cost_samples = _samples(collector, "synthorg_provider_cost")
    assert any(
        labels == {"provider": "example-provider", "model": "example-large-001"}
        for labels, _ in cost_samples
    )


@pytest.mark.usefixtures("_seed_provider_names")
def test_record_provider_usage_folds_unknown_provider() -> None:
    """Regression (audit 132): an unregistered provider id folds to
    ``__unknown__`` rather than minting a permanent time-series child."""
    collector = PrometheusCollector()
    collector.record_provider_usage(
        provider="attacker-" + "x" * 200,
        model="example-medium-001",
        input_tokens=1,
        output_tokens=1,
        cost=0.0,
    )
    cost_samples = _samples(collector, "synthorg_provider_cost")
    providers = {labels["provider"] for labels, _ in cost_samples}
    assert providers == {"__unknown__"}


@pytest.mark.usefixtures("_seed_provider_names")
def test_record_provider_usage_folds_malformed_model() -> None:
    """Regression (audit 132): an over-long / out-of-charset model id folds to
    ``__unknown__`` (the model label cannot be allowlisted, so a length+charset
    cap bounds the cardinality vector)."""
    collector = PrometheusCollector()
    collector.record_provider_usage(
        provider="example-provider",
        model="model with spaces and " + "y" * 200,
        input_tokens=1,
        output_tokens=1,
        cost=0.0,
    )
    cost_samples = _samples(collector, "synthorg_provider_cost")
    models = {labels["model"] for labels, _ in cost_samples}
    assert models == {"__unknown__"}


@pytest.mark.usefixtures("_seed_provider_names")
def test_record_provider_error_bounds_provider_and_model() -> None:
    """Regression (audit 132): the provider-error counter bounds both the
    provider and model labels."""
    collector = PrometheusCollector()
    collector.record_provider_error(
        provider="unregistered-provider",
        model="bad model!",
        error_class="rate_limit",
    )
    error_samples = _samples(collector, "synthorg_provider_errors")
    assert any(
        labels["provider"] == "__unknown__" and labels["model"] == "__unknown__"
        for labels, _ in error_samples
    )


@pytest.mark.usefixtures("_seed_provider_names")
def test_record_provider_call_duration_observes_histogram() -> None:
    """Regression (audit 05): provider call latency is captured for both
    call types (complete recorded only a span attribute before; stream
    recorded nothing)."""
    collector = PrometheusCollector()
    collector.record_provider_call_duration(
        provider="example-provider",
        model="example-large-001",
        call_type="complete",
        duration_sec=1.5,
    )
    samples = _samples(collector, "synthorg_provider_call_duration_seconds")
    assert any(
        labels
        == {
            "provider": "example-provider",
            "model": "example-large-001",
            "call_type": "complete",
        }
        and value == 1.0
        for labels, value in samples
    )


def test_record_provider_call_duration_rejects_unknown_call_type() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown"):
        collector.record_provider_call_duration(
            provider="p",
            model="m",
            call_type="bogus",
            duration_sec=0.0,
        )


# -- record_autonomy_promotion -----------------------------------------------


@pytest.mark.parametrize("outcome", ["granted", "denied"])
def test_record_autonomy_promotion_increments_counter(outcome: str) -> None:
    """Regression (audit 05): the autonomy-promotion workflow's grant/deny
    decisions are now counted, not only logged."""
    collector = PrometheusCollector()
    collector.record_autonomy_promotion(outcome=outcome)
    samples = _samples(collector, "synthorg_autonomy_promotion_decisions")
    assert any(
        labels == {"outcome": outcome} and value == 1.0 for labels, value in samples
    )


def test_record_autonomy_promotion_rejects_unknown_outcome() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown"):
        collector.record_autonomy_promotion(outcome="maybe")


# -- record_budget_query -----------------------------------------------------


def test_record_budget_query_observes_histogram_and_logs() -> None:
    from synthorg.observability.events.budget import BUDGET_QUERY_OUTCOME

    collector = PrometheusCollector()
    with structlog.testing.capture_logs() as logs:
        collector.record_budget_query(
            query_type="total_cost",
            duration_sec=0.005,
        )
    samples = _samples(collector, "synthorg_budget_query_duration_seconds")
    assert any(
        labels == {"query_type": "total_cost"} and value == 1.0
        for labels, value in samples
    )
    assert any(
        rec.get("event") == BUDGET_QUERY_OUTCOME
        and rec.get("query_type") == "total_cost"
        for rec in logs
    )


def test_record_budget_query_rejects_unknown_query_type() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown"):
        collector.record_budget_query(query_type="bogus", duration_sec=0.0)


# -- record_audit_chain_verification -----------------------------------------


def test_record_audit_chain_verification_valid_outcome_logs_and_increments() -> None:
    from synthorg.observability.events.security import (
        SECURITY_AUDIT_CHAIN_VERIFY_OUTCOME,
    )

    collector = PrometheusCollector()
    with structlog.testing.capture_logs() as logs:
        collector.record_audit_chain_verification(
            outcome="valid",
            entries_checked=42,
        )
    samples = _samples(collector, "synthorg_audit_chain_verifications")
    assert any(
        labels == {"outcome": "valid"} and value == 1.0 for labels, value in samples
    )
    assert any(
        rec.get("event") == SECURITY_AUDIT_CHAIN_VERIFY_OUTCOME
        and rec.get("outcome") == "valid"
        and rec.get("entries_checked") == 42
        for rec in logs
    )


def test_record_audit_chain_verification_broken_outcome_logs_and_increments() -> None:
    from synthorg.observability.events.security import (
        SECURITY_AUDIT_CHAIN_VERIFY_OUTCOME,
    )

    collector = PrometheusCollector()
    with structlog.testing.capture_logs() as logs:
        collector.record_audit_chain_verification(
            outcome="broken",
            entries_checked=15,
            first_break_position=8,
        )
    samples = _samples(collector, "synthorg_audit_chain_verifications")
    assert any(
        labels == {"outcome": "broken"} and value == 1.0 for labels, value in samples
    )
    assert any(
        rec.get("event") == SECURITY_AUDIT_CHAIN_VERIFY_OUTCOME
        and rec.get("outcome") == "broken"
        and rec.get("entries_checked") == 15
        and rec.get("first_break_position") == 8
        for rec in logs
    )


def test_record_audit_chain_verification_rejects_unknown_outcome() -> None:
    collector = PrometheusCollector()
    with pytest.raises(ValueError, match="Unknown"):
        collector.record_audit_chain_verification(
            outcome="bogus",
            entries_checked=0,
        )


# -- VALID_SETTINGS_NAMESPACES parity ----------------------------------------


def test_valid_settings_namespaces_matches_definitions_directory() -> None:
    """Allowlist mirrors the closed set of files under settings/definitions/.

    Adding a new namespace file without updating the allowlist would
    silently drop its mutations from the metric. Failing this test is the
    intended forcing function.
    """
    import pkgutil

    from synthorg.observability.prometheus_labels import VALID_SETTINGS_NAMESPACES
    from synthorg.settings import definitions as _settings_definitions

    discovered = frozenset(
        info.name
        for info in pkgutil.iter_modules(_settings_definitions.__path__)
        if not info.name.startswith("_")
    )
    assert discovered == VALID_SETTINGS_NAMESPACES, (
        "VALID_SETTINGS_NAMESPACES drift: "
        f"missing={discovered - VALID_SETTINGS_NAMESPACES} "
        f"extra={VALID_SETTINGS_NAMESPACES - discovered}"
    )


# -- agent gauge label parity ------------------------------------------------


def test_valid_agent_statuses_matches_enum() -> None:
    """The active-agents status allowlist mirrors ``AgentStatus``.

    The allowlist is duplicated as literals to avoid an ``hr`` import in
    ``prometheus_labels``; this test is the forcing function that keeps the
    two in lockstep so a new status cannot silently fold to ``"other"``.
    """
    from synthorg.hr.enums import AgentStatus
    from synthorg.observability.prometheus_label_folds import VALID_AGENT_STATUSES

    assert frozenset(s.value for s in AgentStatus) == VALID_AGENT_STATUSES


def test_valid_trust_levels_matches_enum() -> None:
    """The active-agents trust-level allowlist mirrors ``ToolAccessLevel``."""
    from synthorg.core.tool_constraints import ToolAccessLevel
    from synthorg.observability.prometheus_label_folds import VALID_TRUST_LEVELS

    assert frozenset(t.value for t in ToolAccessLevel) == VALID_TRUST_LEVELS
