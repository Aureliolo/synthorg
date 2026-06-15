"""Memory consolidation -- axis split (selector + op), retention, archival.

Re-exports the public API so consumers can import from
``synthorg.memory.consolidation`` directly. The pre-split monolithic
``Simple`` / ``DualMode`` / ``LLM`` strategy classes were removed in
the ADR-0005 axis split; build a composite via
:func:`build_consolidation_strategy` instead.
"""

from synthorg.memory.consolidation.abstractive import AbstractiveSummarizer
from synthorg.memory.consolidation.archival import ArchivalStore
from synthorg.memory.consolidation.axis import (
    ConsolidationContext,
    ConsolidationOp,
    EntrySelector,
    OpResult,
    SelectionGroup,
)
from synthorg.memory.consolidation.composite import (
    CompositeConsolidationStrategy,
)
from synthorg.memory.consolidation.config import (
    ArchivalConfig,
    ConsolidationConfig,
    ConsolidationStrategyType,
    DualModeConfig,
    LLMConsolidationConfig,
    RetentionConfig,
)
from synthorg.memory.consolidation.density import ContentDensity, DensityClassifier
from synthorg.memory.consolidation.distillation import (
    DistillationRequest,
    MemoryToolName,
    capture_distillation,
)
from synthorg.memory.consolidation.extractive import ExtractivePreserver
from synthorg.memory.consolidation.factory import (
    ConsolidationDeps,
    build_consolidation_strategy,
)
from synthorg.memory.consolidation.llm_op import LLMSynthesisOp, SynthesisOutcome
from synthorg.memory.consolidation.models import (
    ArchivalEntry,
    ArchivalIndexEntry,
    ArchivalMode,
    ArchivalModeAssignment,
    ConsolidationResult,
    RetentionRule,
)
from synthorg.memory.consolidation.ops import (
    ConcatenationOp,
    DensityRoutingOp,
    SingleModeOp,
    abstractive_summarization_op,
    extractive_preservation_op,
)
from synthorg.memory.consolidation.retention import RetentionEnforcer
from synthorg.memory.consolidation.selectors import HighestRelevanceSelector
from synthorg.memory.consolidation.service import MemoryConsolidationService
from synthorg.memory.consolidation.strategy import ConsolidationStrategy

__all__ = [
    "AbstractiveSummarizer",
    "ArchivalConfig",
    "ArchivalEntry",
    "ArchivalIndexEntry",
    "ArchivalMode",
    "ArchivalModeAssignment",
    "ArchivalStore",
    "CompositeConsolidationStrategy",
    "ConcatenationOp",
    "ConsolidationConfig",
    "ConsolidationContext",
    "ConsolidationDeps",
    "ConsolidationOp",
    "ConsolidationResult",
    "ConsolidationStrategy",
    "ConsolidationStrategyType",
    "ContentDensity",
    "DensityClassifier",
    "DensityRoutingOp",
    "DistillationRequest",
    "DualModeConfig",
    "EntrySelector",
    "ExtractivePreserver",
    "HighestRelevanceSelector",
    "LLMConsolidationConfig",
    "LLMSynthesisOp",
    "MemoryConsolidationService",
    "MemoryToolName",
    "OpResult",
    "RetentionConfig",
    "RetentionEnforcer",
    "RetentionRule",
    "SelectionGroup",
    "SingleModeOp",
    "SynthesisOutcome",
    "abstractive_summarization_op",
    "build_consolidation_strategy",
    "capture_distillation",
    "extractive_preservation_op",
]
