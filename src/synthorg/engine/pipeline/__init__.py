"""Work pipeline spine.

The single coherent path from "work enters" to "agents execute it":
intake -> projects -> decompose (solo-vs-team verdict) -> solo or
team execution -> coordination metrics. The spine is the one
integration point every entry adapter feeds via a typed
:class:`WorkItem`.
"""

from synthorg.engine.pipeline.factory import build_work_pipeline
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.pipeline.service import DefaultWorkPipeline

__all__ = [
    "DefaultWorkPipeline",
    "ExecutionPath",
    "RoutingVerdict",
    "WorkItem",
    "WorkPhaseResult",
    "WorkPipeline",
    "WorkPipelineResult",
    "WorkSource",
    "build_work_pipeline",
]
