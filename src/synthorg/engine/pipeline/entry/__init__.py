"""Work-entry adapters: thin seams that feed the pipeline spine.

Every real work-entry path (client-request intake; human-stated
goals/objectives; the task board and conversational interfaces in
sibling work) maps its own domain input onto a
:class:`~synthorg.engine.pipeline.models.WorkItem` and calls
:meth:`WorkPipeline.run`. Adapters own no pipeline logic and no
source-entity reconciliation; the caller (controller / background
task) owns lifecycle persistence.
"""

from synthorg.engine.pipeline.entry.factory import build_work_entry_adapter
from synthorg.engine.pipeline.entry.intake_adapter import IntakeEntryAdapter
from synthorg.engine.pipeline.entry.objective_adapter import (
    ObjectiveEntryAdapter,
    ObjectiveSubmission,
)
from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter

__all__ = [
    "IntakeEntryAdapter",
    "ObjectiveEntryAdapter",
    "ObjectiveSubmission",
    "WorkEntryAdapter",
    "build_work_entry_adapter",
]
