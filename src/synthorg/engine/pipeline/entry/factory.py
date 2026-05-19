"""Factory for source-keyed work-entry adapters.

Dispatch on :class:`WorkSource`. This child ships the ``INTAKE`` arm;
sibling work adds ``TASK_BOARD`` / ``OBJECTIVE`` arms here. A source
with no adapter is a hard error (no silent default), matching the
project-wide pluggable-subsystems contract.
"""

from typing import TYPE_CHECKING

from synthorg.client.factory import UnknownStrategyError
from synthorg.engine.pipeline.entry.intake_adapter import IntakeEntryAdapter
from synthorg.engine.pipeline.models import WorkSource

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter
    from synthorg.engine.pipeline.protocol import WorkPipeline


def build_work_entry_adapter(
    source: WorkSource,
    *,
    work_pipeline: WorkPipeline,
    default_project: NotBlankStr,
) -> WorkEntryAdapter:
    """Construct the work-entry adapter for ``source``.

    Args:
        source: The originating work source discriminator.
        work_pipeline: The composed pipeline spine to drive.
        default_project: Project intake work items are filed into.

    Returns:
        The concrete :class:`WorkEntryAdapter`.

    Raises:
        UnknownStrategyError: If ``source`` has no wired adapter.
    """
    if source is WorkSource.INTAKE:
        return IntakeEntryAdapter(
            work_pipeline=work_pipeline,
            default_project=default_project,
        )
    msg = f"no work-entry adapter wired for source {source.value!r}"
    raise UnknownStrategyError(msg)
