"""A small staged-transformation pipeline used by the loop A/B benchmark."""

from pipeline.registry import REGISTRY, get_stage
from pipeline.stage import Stage
from pipeline.stages import Double

__all__ = ["REGISTRY", "Double", "Stage", "get_stage"]
