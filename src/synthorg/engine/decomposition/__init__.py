"""Task decomposition engine.

Breaks complex tasks into subtasks with dependency tracking,
classifies task structure, and manages status rollup.
"""

from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.dag import DependencyGraph
from synthorg.engine.decomposition.llm import (
    LlmDecompositionConfig,
    LlmDecompositionStrategy,
)
from synthorg.engine.decomposition.manual import ManualDecompositionStrategy
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.decomposition.rollup import StatusRollup
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.decomposition.status_rollup import SubtaskStatusRollup

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
