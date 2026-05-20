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


@pytest.mark.parametrize(
    ("source", "expected_type"),
    [
        (WorkSource.INTAKE, IntakeEntryAdapter),
        (WorkSource.OBJECTIVE, ObjectiveEntryAdapter),
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
    ],
)
def test_unwired_source_is_hard_error(source: WorkSource) -> None:
    with pytest.raises(UnknownStrategyError, match=source.value):
        build_work_entry_adapter(
            source,
            work_pipeline=mock_of[WorkPipeline](),
            default_project="client-intake",
        )
