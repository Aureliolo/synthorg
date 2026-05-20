"""Factory for source-keyed work-entry adapters.

Dispatch on :class:`WorkSource`. Ships the ``INTAKE``, ``OBJECTIVE``,
and ``TASK_BOARD`` arms; sibling work adds ``CONVERSATIONAL`` here.
A source with no adapter is a hard error (no silent default),
matching the project-wide pluggable-subsystems contract.

``default_project`` is consumed by :class:`IntakeEntryAdapter` and
:class:`ObjectiveEntryAdapter`. The task-board input carries its own
project, so the TASK_BOARD arm ignores it. The kwarg stays required
on the factory signature so all boot wiring goes through a single
uniform call site.

Overloads narrow the return type to the concrete adapter when the
caller passes a literal :class:`WorkSource` member so boot helpers
hand the right concrete adapter to the right ``AppState`` seam
without a manual cast.
"""

from typing import TYPE_CHECKING, Any, Literal, overload

from synthorg.client.factory import UnknownStrategyError
from synthorg.engine.pipeline.entry.intake_adapter import IntakeEntryAdapter
from synthorg.engine.pipeline.entry.objective_adapter import ObjectiveEntryAdapter
from synthorg.engine.pipeline.entry.task_board_adapter import TaskBoardEntryAdapter
from synthorg.engine.pipeline.models import WorkSource

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter
    from synthorg.engine.pipeline.protocol import WorkPipeline


@overload
def build_work_entry_adapter(
    source: Literal[WorkSource.INTAKE],
    *,
    work_pipeline: WorkPipeline,
    default_project: NotBlankStr,
) -> IntakeEntryAdapter: ...


@overload
def build_work_entry_adapter(
    source: Literal[WorkSource.OBJECTIVE],
    *,
    work_pipeline: WorkPipeline,
    default_project: NotBlankStr,
) -> ObjectiveEntryAdapter: ...


@overload
def build_work_entry_adapter(
    source: Literal[WorkSource.TASK_BOARD],
    *,
    work_pipeline: WorkPipeline,
    default_project: NotBlankStr,
) -> TaskBoardEntryAdapter: ...


@overload
def build_work_entry_adapter(
    source: WorkSource,
    *,
    work_pipeline: WorkPipeline,
    default_project: NotBlankStr,
) -> WorkEntryAdapter[Any]: ...


def build_work_entry_adapter(
    source: WorkSource,
    *,
    work_pipeline: WorkPipeline,
    default_project: NotBlankStr,
) -> WorkEntryAdapter[Any]:
    """Construct the work-entry adapter for ``source``.

    Args:
        source: The originating work source discriminator.
        work_pipeline: The composed pipeline spine to drive.
        default_project: Project work items are filed into. For
            ``INTAKE`` this is the client-intake project; for
            ``OBJECTIVE`` it is the objectives default project; the
            TASK_BOARD arm ignores it (board filings carry their own
            project). The kwarg stays required so all boot wiring
            calls share a single uniform shape.

    Returns:
        The concrete work-entry adapter.

    Raises:
        UnknownStrategyError: If ``source`` has no wired adapter.
    """
    if source is WorkSource.INTAKE:
        return IntakeEntryAdapter(
            work_pipeline=work_pipeline,
            default_project=default_project,
        )
    if source is WorkSource.OBJECTIVE:
        return ObjectiveEntryAdapter(
            work_pipeline=work_pipeline,
            default_project=default_project,
        )
    if source is WorkSource.TASK_BOARD:
        return TaskBoardEntryAdapter(work_pipeline=work_pipeline)
    msg = f"no work-entry adapter wired for source {source.value!r}"
    raise UnknownStrategyError(msg)
