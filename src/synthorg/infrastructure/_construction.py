# module-kind: code
"""Facades feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.infrastructure.state import FacadesStateSlice

if TYPE_CHECKING:
    # Cycle breaker: ``api.construction_wiring`` pulls a cold-import cycle, so
    # ``ConstructionDeps`` is named for signatures only.
    from synthorg.api.construction_wiring import ConstructionDeps


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Wire the MCP quality + review facades onto the facades slice.

    ``ReviewFacadeService`` is dependency-free; ``QualityFacadeService``
    projects the construction-wired performance tracker, so it is wired
    only when that tracker is present (it always is on the production
    boot path). Partial ``wire`` preserves the other facade fields that
    sibling wirers populate.
    """
    from synthorg.engine.quality.mcp_services import (  # noqa: PLC0415
        QualityFacadeService,
        ReviewFacadeService,
    )

    app_state.wire(
        FacadesStateSlice,
        review_facade_service=ReviewFacadeService(),
    )
    if deps.performance_tracker is not None:
        app_state.wire(
            FacadesStateSlice,
            quality_facade_service=QualityFacadeService(
                tracker=deps.performance_tracker,
            ),
        )
