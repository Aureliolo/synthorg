"""Agent memory system — protocols, models, config, and factory.

Re-exports protocols (``MemoryBackend``, ``MemoryCapabilities``,
``SharedKnowledgeStore``, ``MemoryInjectionStrategy``,
``OrgMemoryBackend``, ``ConsolidationStrategy``, ``ArchivalStore``),
concrete backends (``Mem0MemoryBackend``), domain models, config
models, factory, retrieval pipeline, consolidation, org memory, and
error hierarchy so consumers can import from ``synthorg.memory``
directly.
"""

from synthorg.memory.backends.mem0 import (
    Mem0EmbedderConfig,
    Mem0MemoryBackend,
)
from synthorg.memory.capabilities import MemoryCapabilities
from synthorg.memory.config import (
    CompanyMemoryConfig,
    MemoryOptionsConfig,
    MemoryStorageConfig,
)
from synthorg.memory.consolidation import (
    ArchivalStore,
    ConsolidationConfig,
    ConsolidationResult,
    ConsolidationStrategy,
    MemoryConsolidationService,
    RetentionEnforcer,
    SimpleConsolidationStrategy,
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
    OrgFact,
    OrgFactAuthor,
    OrgFactStore,
    OrgFactWriteRequest,
    OrgMemoryBackend,
    OrgMemoryConfig,
    OrgMemoryError,
    OrgMemoryQuery,
    SQLiteOrgFactStore,
    create_org_memory_backend,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.ranking import ScoredMemory
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.shared import SharedKnowledgeStore

__all__ = [
    "ArchivalStore",
    "CompanyMemoryConfig",
    "ConsolidationConfig",
    "ConsolidationResult",
    "ConsolidationStrategy",
    "ContextInjectionStrategy",
    "DefaultTokenEstimator",
    "InjectionPoint",
    "InjectionStrategy",
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
    "OrgFactStore",
    "OrgFactWriteRequest",
    "OrgMemoryBackend",
    "OrgMemoryConfig",
    "OrgMemoryError",
    "OrgMemoryQuery",
    "RetentionEnforcer",
    "SQLiteOrgFactStore",
    "ScoredMemory",
    "SharedKnowledgeStore",
    "SimpleConsolidationStrategy",
    "TokenEstimator",
    "create_memory_backend",
    "create_org_memory_backend",
]
