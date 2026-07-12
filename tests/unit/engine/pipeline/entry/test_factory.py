"""Unit coverage for the work-entry adapter factories.

:func:`build_work_entry_adapter` dispatches on :class:`WorkSource`; a
source with no wired adapter is a hard ``UnknownStrategyError`` (no
silent default). ``OBJECTIVE`` deliberately does not route through it:
the objective adapter mints a per-initiative project, so it has its own
:func:`build_objective_entry_adapter` with a project-repo collaborator.
"""

import pytest
import structlog.testing

from synthorg.client.factory import UnknownStrategyError
from synthorg.engine.pipeline.entry.factory import (
    build_objective_entry_adapter,
    build_work_entry_adapter,
)
from synthorg.engine.pipeline.entry.intake_adapter import IntakeEntryAdapter
from synthorg.engine.pipeline.entry.objective_adapter import ObjectiveEntryAdapter
from synthorg.engine.pipeline.entry.task_board_adapter import TaskBoardEntryAdapter
from synthorg.engine.pipeline.models import WorkSource
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.observability.events.pipeline import PIPELINE_ENTRY_UNKNOWN_SOURCE
from synthorg.persistence.project_protocol import ProjectRepository
from tests._shared import mock_of

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("source", "expected_type"),
    [
        (WorkSource.INTAKE, IntakeEntryAdapter),
        (WorkSource.TASK_BOARD, TaskBoardEntryAdapter),
    ],
)
def test_wired_source_builds_concrete_adapter(
    source: WorkSource,
    expected_type: type,
) -> None:
    """Every wired source builds its concrete adapter and stamps ``source``."""
    adapter = build_work_entry_adapter(
        source,
        work_pipeline=mock_of[WorkPipeline](),
        default_project="client-intake",
    )
    assert isinstance(adapter, expected_type)
    assert adapter.source is source


@pytest.mark.parametrize(
    "source",
    [
        WorkSource.SIMULATION,
        WorkSource.CONVERSATIONAL,
        WorkSource.OBJECTIVE,
    ],
)
def test_unwired_source_is_hard_error(source: WorkSource) -> None:
    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(UnknownStrategyError, match=source.value),
    ):
        build_work_entry_adapter(
            source,
            work_pipeline=mock_of[WorkPipeline](),
            default_project="client-intake",
        )
    assert any(
        e["event"] == PIPELINE_ENTRY_UNKNOWN_SOURCE and e.get("source") == source.value
        for e in logs
    )


def test_build_objective_entry_adapter() -> None:
    """The dedicated objective builder wires the repo-backed adapter."""
    adapter = build_objective_entry_adapter(
        work_pipeline=mock_of[WorkPipeline](),
        project_repo=mock_of[ProjectRepository](),
    )
    assert isinstance(adapter, ObjectiveEntryAdapter)
    assert adapter.source is WorkSource.OBJECTIVE
