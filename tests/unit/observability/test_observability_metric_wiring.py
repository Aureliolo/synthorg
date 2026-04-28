"""Integration tests for the new observability metric emissions.

Covers the issue #1601 acceptance criterion that every wired metric
is exercised end-to-end:

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

from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
import structlog.testing
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

from synthorg.observability.events.metrics import (
    CLIENT_DISCONNECTED,
    METRICS_SCRAPE_FAILED,
)
from synthorg.observability.prometheus_collector import PrometheusCollector
from synthorg.observability.prometheus_labels import (
    _LabelSnapshot,
    is_known_agent_id,
    update_label_snapshot,
)

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


def test_is_known_agent_id_passes_in_bootstrap_mode() -> None:
    # Bootstrap (unseeded) snapshot accepts any value so the very
    # first scrape doesn't drop every push-time metric.
    assert is_known_agent_id("anything") is True


def test_is_known_agent_id_rejects_after_seed() -> None:
    update_label_snapshot(
        _LabelSnapshot(
            agent_ids=frozenset({"agent-1"}),
            seeded=True,
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
) -> MagicMock:
    state = MagicMock()
    type(state).has_cost_tracker = PropertyMock(return_value=False)
    type(state).has_agent_registry = PropertyMock(return_value=True)
    type(state).has_task_engine = PropertyMock(return_value=False)
    type(state).has_config_resolver = PropertyMock(return_value=False)
    state.agent_registry.list_active = AsyncMock(return_value=agents)

    state.persistence = MagicMock()
    if workflow_repo_raises:
        state.persistence.workflow_definitions.list_definitions = AsyncMock(
            side_effect=RuntimeError("workflow repo down"),
        )
    else:
        state.persistence.workflow_definitions.list_definitions = AsyncMock(
            return_value=(MagicMock(id="wf-1"),),
        )

    if department_service_raises:
        state.department_service.list_departments = AsyncMock(
            side_effect=RuntimeError("dept service down"),
        )
    else:
        state.department_service.list_departments = AsyncMock(
            return_value=((MagicMock(name="engineering"),), 1),
        )
    return state


async def test_rebuild_label_snapshot_uses_live_registries() -> None:
    collector = PrometheusCollector()
    agents = (_stub_agent("agent-1"), _stub_agent("agent-2"))
    state = _stub_app_state(agents=agents)
    await collector.refresh(state)

    # After refresh the snapshot is seeded; downstream validation
    # rejects unknown ids.
    assert is_known_agent_id("agent-1") is True
    assert is_known_agent_id("agent-99") is False


async def test_rebuild_label_snapshot_partial_when_workflow_repo_fails(
    caplog: Any,
) -> None:
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
