"""Unit coverage for :func:`build_work_entry_adapter`.

Dispatch is on :class:`WorkSource`; a source with no wired adapter is
a hard ``UnknownStrategyError`` (no silent default).
"""

import pytest

from synthorg.client.factory import UnknownStrategyError
from synthorg.engine.pipeline.entry.factory import build_work_entry_adapter
from synthorg.engine.pipeline.entry.intake_adapter import IntakeEntryAdapter
from synthorg.engine.pipeline.entry.objective_adapter import ObjectiveEntryAdapter
from synthorg.engine.pipeline.entry.task_board_adapter import TaskBoardEntryAdapter
from synthorg.engine.pipeline.models import WorkSource
from synthorg.engine.pipeline.protocol import WorkPipeline
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def test_intake_source_builds_intake_adapter() -> None:
    adapter = build_work_entry_adapter(
        WorkSource.INTAKE,
        work_pipeline=mock_of[WorkPipeline](),
        default_project="client-intake",
    )
    assert isinstance(adapter, IntakeEntryAdapter)
    assert adapter.source is WorkSource.INTAKE


def test_objective_source_builds_objective_adapter() -> None:
    adapter = build_work_entry_adapter(
        WorkSource.OBJECTIVE,
        work_pipeline=mock_of[WorkPipeline](),
        default_project="objectives",
    )
    assert isinstance(adapter, ObjectiveEntryAdapter)
    assert adapter.source is WorkSource.OBJECTIVE


def test_task_board_source_builds_task_board_adapter() -> None:
    adapter = build_work_entry_adapter(
        WorkSource.TASK_BOARD,
        work_pipeline=mock_of[WorkPipeline](),
        default_project="client-intake",
    )
    assert isinstance(adapter, TaskBoardEntryAdapter)
    assert adapter.source is WorkSource.TASK_BOARD


@pytest.mark.parametrize(
    "source",
    [
        WorkSource.SIMULATION,
        WorkSource.CONVERSATIONAL,
    ],
)
def test_unwired_source_is_hard_error(source: WorkSource) -> None:
    with pytest.raises(UnknownStrategyError, match=source.value):
        build_work_entry_adapter(
            source,
            work_pipeline=mock_of[WorkPipeline](),
            default_project="client-intake",
        )
