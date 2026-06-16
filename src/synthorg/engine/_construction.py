# module-kind: code
"""Engine feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.engine.state import EngineStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the engine slice (task engine, pipeline, entry adapters).

    Also constructs the in-memory error-taxonomy aggregation store: a pure
    ring buffer shared between the post-execution classification sinks
    (writers, wired into the boot engine) and the signals error aggregator
    (reader), so both observe the same taxonomy.
    """
    from synthorg.engine.classification.taxonomy_store import (  # noqa: PLC0415
        InMemoryErrorTaxonomyStore,
    )

    app_state.swap_slice(
        EngineStateSlice.model_construct(
            task_engine=deps.phase1.task_engine,
            work_pipeline=deps.work_pipeline,
            ceremony_scheduler=deps.meeting_wire.ceremony_scheduler,
            intake_entry_adapter=deps.intake_entry_adapter,
            task_board_entry_adapter=deps.task_board_entry_adapter,
            error_taxonomy_store=InMemoryErrorTaxonomyStore(),
        )
    )
