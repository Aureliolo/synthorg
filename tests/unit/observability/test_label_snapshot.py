"""Tests for the snapshot-backed Prometheus label validators.

The snapshot pattern lets sync ``record_*`` call sites validate
``agent_id``, ``workflow_definition_id``, and ``department`` against a
process-global ``frozenset`` that the async pre-scrape ``refresh()``
seeds from the live runtime registries. Until the first seed, the
snapshot is in bootstrap mode and every value passes through; once
seeded, unknown values raise ``ValueError`` with a single
``METRICS_SCRAPE_FAILED`` WARN.
"""

from collections.abc import Iterator

import pytest
import structlog.testing

from synthorg.observability.events.metrics import METRICS_SCRAPE_FAILED
from synthorg.observability.prometheus_labels import (
    _LabelSnapshot,
    _reset_label_snapshot_for_tests,
    update_label_snapshot,
    validate_agent_id,
    validate_department,
    validate_workflow_definition_id,
)


@pytest.fixture(autouse=True)
def _bootstrap_snapshot() -> Iterator[None]:
    """Reset the module-global snapshot before AND after each test.

    Resetting only before the test would leave the last test's
    seeded snapshot in place, which leaks into other test modules
    that exercise push-time recording paths.
    """
    _reset_label_snapshot_for_tests()
    yield
    _reset_label_snapshot_for_tests()


@pytest.mark.unit
def test_validate_agent_id_is_noop_until_snapshot_seeded() -> None:
    # Bootstrap mode: any value passes so the first scrape doesn't
    # nuke every push-time metric before refresh() runs.
    validate_agent_id("agent-anything")


@pytest.mark.unit
def test_validate_agent_id_rejects_unknown_after_seed() -> None:
    update_label_snapshot(
        _LabelSnapshot(
            agent_ids=frozenset({"agent-1", "agent-2"}),
            seeded=True,
        ),
    )
    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(ValueError, match="agent_id"),
    ):
        validate_agent_id("agent-unknown")
    assert any(
        rec.get("event") == METRICS_SCRAPE_FAILED
        and rec.get("rejected_value") == "agent-unknown"
        for rec in logs
    )


@pytest.mark.unit
def test_validate_agent_id_accepts_known_after_seed() -> None:
    update_label_snapshot(
        _LabelSnapshot(
            agent_ids=frozenset({"agent-1"}),
            seeded=True,
        ),
    )
    validate_agent_id("agent-1")


@pytest.mark.unit
def test_update_label_snapshot_replaces_atomically() -> None:
    update_label_snapshot(
        _LabelSnapshot(
            agent_ids=frozenset({"agent-1"}),
            seeded=True,
        ),
    )
    validate_agent_id("agent-1")

    update_label_snapshot(
        _LabelSnapshot(
            agent_ids=frozenset({"agent-2"}),
            seeded=True,
        ),
    )
    with pytest.raises(ValueError, match="agent_id"):
        validate_agent_id("agent-1")
    validate_agent_id("agent-2")


@pytest.mark.unit
def test_validate_workflow_definition_id_rejects_unknown_after_seed() -> None:
    update_label_snapshot(
        _LabelSnapshot(
            workflow_definition_ids=frozenset({"wf-onboarding"}),
            seeded=True,
        ),
    )
    with pytest.raises(ValueError, match="workflow_definition_id"):
        validate_workflow_definition_id("wf-unknown")
    validate_workflow_definition_id("wf-onboarding")


@pytest.mark.unit
def test_validate_department_rejects_unknown_after_seed() -> None:
    update_label_snapshot(
        _LabelSnapshot(
            departments=frozenset({"engineering"}),
            seeded=True,
        ),
    )
    with pytest.raises(ValueError, match="department"):
        validate_department("ops")
    validate_department("engineering")


@pytest.mark.unit
def test_seeded_snapshot_with_empty_set_rejects_everything() -> None:
    # Genuinely-empty registry (no agents) is distinct from
    # bootstrap mode -- a real seed with zero entries means every
    # incoming agent_id is unknown by construction.
    update_label_snapshot(_LabelSnapshot(seeded=True))
    with pytest.raises(ValueError, match="agent_id"):
        validate_agent_id("agent-1")
