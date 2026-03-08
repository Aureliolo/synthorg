"""Task decomposition engine.

Breaks complex tasks into subtasks with dependency tracking,
classifies task structure, and manages status rollup.
"""

from ai_company.engine.decomposition.classifier import TaskStructureClassifier
from ai_company.engine.decomposition.dag import DependencyGraph
from ai_company.engine.decomposition.manual import ManualDecompositionStrategy
from ai_company.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
    SubtaskStatusRollup,
)
from ai_company.engine.decomposition.protocol import DecompositionStrategy
from ai_company.engine.decomposition.rollup import StatusRollup
from ai_company.engine.decomposition.service import DecompositionService

__all__ = [
    "DecompositionContext",
    "DecompositionPlan",
    "DecompositionResult",
    "DecompositionService",
    "DecompositionStrategy",
    "DependencyGraph",
    "ManualDecompositionStrategy",
    "StatusRollup",
    "SubtaskDefinition",
    "SubtaskStatusRollup",
    "TaskStructureClassifier",
]
