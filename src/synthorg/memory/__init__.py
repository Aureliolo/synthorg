"""Agent memory system -- protocols, models, config, and factory.

Re-exports protocols (``MemoryBackend``, ``MemoryCapabilities``,
``SharedKnowledgeStore``, ``MemoryInjectionStrategy``,
``OrgMemoryBackend``, ``ConsolidationStrategy``, ``ArchivalStore``),
concrete backends (``Mem0MemoryBackend``), domain models, config
models, factory, retrieval pipeline, consolidation, org memory, and
error hierarchy so consumers can import from ``synthorg.memory``
directly.

Hybrid search: ``BM25Tokenizer``, ``SparseVector``,
``FusionStrategy``, ``fuse_ranked_lists``.

Tool-based strategy: ``ToolBasedInjectionStrategy``.

Query reformulation: ``QueryReformulator``, ``SufficiencyChecker``,
``LLMQueryReformulator``, ``LLMSufficiencyChecker``.
"""

from synthorg.memory.backends.mem0 import (
    Mem0EmbedderConfig,
    Mem0MemoryBackend,
)
from synthorg.memory.capabilities import MemoryCapabilities
from synthorg.memory.config import (
    CompanyMemoryConfig,
    EmbedderOverrideConfig,
    MemoryOptionsConfig,
    MemoryStorageConfig,
)
from synthorg.memory.consolidation import (
    ArchivalMode,
    ArchivalStore,
    ConsolidationConfig,
    ConsolidationResult,
    ConsolidationStrategy,
    ConsolidationStrategyType,
    ContentDensity,
    MemoryConsolidationService,
    RetentionEnforcer,
    build_consolidation_strategy,
)
from synthorg.memory.errors import (
    MemoryCapabilityError,
    MemoryConfigError,
    MemoryConnectionError,
    MemoryError,  # noqa: A004
    MemoryNotFoundError,
    MemoryRetrievalError,
    MemoryStoreError,
)
from synthorg.memory.factory import create_memory_backend
from synthorg.memory.injection import (
    DefaultTokenEstimator,
    InjectionPoint,
    InjectionStrategy,
    MemoryInjectionStrategy,
    TokenEstimator,
)
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.memory.org import (
    HybridPromptRetrievalBackend,
    OrgFact,
    OrgFactAuthor,
    OrgFactRepository,
    OrgFactWriteRequest,
    OrgMemoryBackend,
    OrgMemoryConfig,
    OrgMemoryError,
    OrgMemoryQuery,
)
from synthorg.memory.procedural import (
    FailureAnalysisPayload,
    ProceduralMemoryConfig,
    ProceduralMemoryProposal,
    ProceduralMemoryProposer,
    materialize_skill_md,
    propose_procedural_memory,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.ranking import FusionStrategy, ScoredMemory, fuse_ranked_lists
from synthorg.memory.reformulation import (
    LLMQueryReformulator,
    LLMSufficiencyChecker,
    QueryReformulator,
    SufficiencyChecker,
)
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.self_editing import (
    SelfEditingMemoryConfig,
    SelfEditingMemoryStrategy,
)
from synthorg.memory.shared import SharedKnowledgeStore
from synthorg.memory.sparse import BM25Tokenizer, SparseVector
from synthorg.memory.tool_retriever import ToolBasedInjectionStrategy

__all__ = [
    "ArchivalMode",
    "ArchivalStore",
    # Hybrid search
    "BM25Tokenizer",
    "CompanyMemoryConfig",
    "ConsolidationConfig",
    "ConsolidationResult",
    "ConsolidationStrategy",
    "ConsolidationStrategyType",
    "ContentDensity",
    "ContextInjectionStrategy",
    "DefaultTokenEstimator",
    "EmbedderOverrideConfig",
    # Procedural memory
    "FailureAnalysisPayload",
    "FusionStrategy",
    "HybridPromptRetrievalBackend",
    "InjectionPoint",
    "InjectionStrategy",
    # Query reformulation
    "LLMQueryReformulator",
    "LLMSufficiencyChecker",
    "Mem0EmbedderConfig",
    "Mem0MemoryBackend",
    "MemoryBackend",
    "MemoryCapabilities",
    "MemoryCapabilityError",
    "MemoryConfigError",
    "MemoryConnectionError",
    "MemoryConsolidationService",
    "MemoryEntry",
    "MemoryError",
    "MemoryInjectionStrategy",
    "MemoryMetadata",
    "MemoryNotFoundError",
    "MemoryOptionsConfig",
    "MemoryQuery",
    "MemoryRetrievalConfig",
    "MemoryRetrievalError",
    "MemoryStorageConfig",
    "MemoryStoreError",
    "MemoryStoreRequest",
    "OrgFact",
    "OrgFactAuthor",
    "OrgFactRepository",
    "OrgFactWriteRequest",
    "OrgMemoryBackend",
    "OrgMemoryConfig",
    "OrgMemoryError",
    "OrgMemoryQuery",
    "ProceduralMemoryConfig",
    "ProceduralMemoryProposal",
    "ProceduralMemoryProposer",
    "QueryReformulator",
    "RetentionEnforcer",
    "ScoredMemory",
    # Self-editing strategy
    "SelfEditingMemoryConfig",
    "SelfEditingMemoryStrategy",
    "SharedKnowledgeStore",
    "SparseVector",
    "SufficiencyChecker",
    "TokenEstimator",
    # Tool-based strategy
    "ToolBasedInjectionStrategy",
    "build_consolidation_strategy",
    "create_memory_backend",
    "fuse_ranked_lists",
    "materialize_skill_md",
    "propose_procedural_memory",
]
