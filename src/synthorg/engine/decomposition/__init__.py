"""Task decomposition engine.

Breaks complex tasks into subtasks with dependency tracking,
classifies task structure, and manages status rollup.
"""

from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.dag import DependencyGraph
from synthorg.engine.decomposition.llm import (
    LlmDecompositionConfig,
    LlmDecompositionStrategy,
)
from synthorg.engine.decomposition.manual import ManualDecompositionStrategy
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
    SubtaskStatusRollup,
)
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.decomposition.rollup import StatusRollup
from synthorg.engine.decomposition.service import DecompositionService

__all__ = [
    "DecompositionContext",
    "DecompositionPlan",
    "DecompositionResult",
    "DecompositionService",
    "DecompositionStrategy",
    "DependencyGraph",
    "LlmDecompositionConfig",
    "LlmDecompositionStrategy",
    "ManualDecompositionStrategy",
    "StatusRollup",
    "SubtaskDefinition",
    "SubtaskStatusRollup",
    "TaskStructureClassifier",
]
