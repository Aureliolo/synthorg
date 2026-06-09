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

from typing import Literal, overload

from synthorg.client.factory import UnknownStrategyError
from synthorg.core.types import NotBlankStr
from synthorg.engine.brownfield.service import BrownfieldImportService
from synthorg.engine.pipeline.entry.brownfield_adapter import BrownfieldEntryAdapter
from synthorg.engine.pipeline.entry.intake_adapter import IntakeEntryAdapter
from synthorg.engine.pipeline.entry.objective_adapter import ObjectiveEntryAdapter
from synthorg.engine.pipeline.entry.task_board_adapter import TaskBoardEntryAdapter
from synthorg.engine.pipeline.forecast_gate import ForecastGate
from synthorg.engine.pipeline.models import WorkSource
from synthorg.engine.pipeline.protocol import WorkPipeline


@overload
def build_work_entry_adapter(
    source: Literal[WorkSource.INTAKE],
    *,
    work_pipeline: WorkPipeline,
    default_project: NotBlankStr,
    forecast_gate: ForecastGate | None = ...,
) -> IntakeEntryAdapter: ...


@overload
def build_work_entry_adapter(
    source: Literal[WorkSource.OBJECTIVE],
    *,
    work_pipeline: WorkPipeline,
    default_project: NotBlankStr,
    forecast_gate: ForecastGate | None = ...,
) -> ObjectiveEntryAdapter: ...


@overload
def build_work_entry_adapter(
    source: Literal[WorkSource.TASK_BOARD],
    *,
    work_pipeline: WorkPipeline,
    default_project: NotBlankStr,
    forecast_gate: ForecastGate | None = ...,
) -> TaskBoardEntryAdapter: ...


@overload
def build_work_entry_adapter(
    source: WorkSource,
    *,
    work_pipeline: WorkPipeline,
    default_project: NotBlankStr,
    forecast_gate: ForecastGate | None = ...,
) -> IntakeEntryAdapter | ObjectiveEntryAdapter | TaskBoardEntryAdapter: ...


def build_work_entry_adapter(
    source: WorkSource,
    *,
    work_pipeline: WorkPipeline,
    default_project: NotBlankStr,
    forecast_gate: ForecastGate | None = None,
) -> IntakeEntryAdapter | ObjectiveEntryAdapter | TaskBoardEntryAdapter:
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
        forecast_gate: Optional pre-flight cost forecast gate. When
            present, the adapter dispatches through the gate (which
            consults the persisted ``Forecast`` row before passing
            the work item to the pipeline); when absent, the adapter
            dispatches straight to the pipeline.

    Returns:
        The concrete work-entry adapter.

    Raises:
        UnknownStrategyError: If ``source`` has no wired adapter.
    """
    spine: WorkPipeline = forecast_gate if forecast_gate is not None else work_pipeline
    if source is WorkSource.INTAKE:
        return IntakeEntryAdapter(
            work_pipeline=spine,
            default_project=default_project,
        )
    if source is WorkSource.OBJECTIVE:
        return ObjectiveEntryAdapter(
            work_pipeline=spine,
            default_project=default_project,
        )
    if source is WorkSource.TASK_BOARD:
        return TaskBoardEntryAdapter(work_pipeline=spine)
    msg = f"no work-entry adapter wired for source {source.value!r}"
    raise UnknownStrategyError(msg)


def build_brownfield_entry_adapter(
    *,
    work_pipeline: WorkPipeline,
    import_service: BrownfieldImportService,
    forecast_gate: ForecastGate | None = None,
) -> BrownfieldEntryAdapter:
    """Construct the brownfield codebase-intake entry adapter.

    Separate from :func:`build_work_entry_adapter` because the brownfield
    adapter needs the :class:`BrownfieldImportService` collaborator (it
    imports / scans / indexes before driving the spine), which the
    uniform generic-adapter signature does not carry.

    Args:
        work_pipeline: The composed pipeline spine to drive.
        import_service: Service that imports, scans, and indexes the codebase.
        forecast_gate: Optional pre-flight cost forecast gate; when present,
            the analysis pass dispatches through it before the spine.

    Returns:
        The brownfield entry adapter.
    """
    spine: WorkPipeline = forecast_gate if forecast_gate is not None else work_pipeline
    return BrownfieldEntryAdapter(
        work_pipeline=spine,
        import_service=import_service,
    )
