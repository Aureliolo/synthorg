"""Agent memory system — protocols, models, config, and factory.

Re-exports protocols (``MemoryBackend``, ``MemoryCapabilities``,
``SharedKnowledgeStore``, ``MemoryInjectionStrategy``,
``OrgMemoryBackend``, ``ConsolidationStrategy``, ``ArchivalStore``),
concrete backends (``Mem0MemoryBackend``), domain models, config
models, factory, retrieval pipeline, consolidation, org memory, and
error hierarchy so consumers can import from ``ai_company.memory``
directly.
"""

from ai_company.memory.backends.mem0 import Mem0MemoryBackend
from ai_company.memory.capabilities import MemoryCapabilities
from ai_company.memory.config import (
    CompanyMemoryConfig,
    MemoryOptionsConfig,
    MemoryStorageConfig,
)
from ai_company.memory.consolidation import (
    ArchivalStore,
    ConsolidationConfig,
    ConsolidationResult,
    ConsolidationStrategy,
    MemoryConsolidationService,
    RetentionEnforcer,
    SimpleConsolidationStrategy,
)
from ai_company.memory.errors import (
    MemoryCapabilityError,
    MemoryConfigError,
    MemoryConnectionError,
    MemoryError,  # noqa: A004
    MemoryNotFoundError,
    MemoryRetrievalError,
    MemoryStoreError,
)
from ai_company.memory.factory import create_memory_backend
from ai_company.memory.injection import (
    DefaultTokenEstimator,
    InjectionPoint,
    InjectionStrategy,
    MemoryInjectionStrategy,
    TokenEstimator,
)
from ai_company.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from ai_company.memory.org import (
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
from ai_company.memory.protocol import MemoryBackend
from ai_company.memory.ranking import ScoredMemory
from ai_company.memory.retrieval_config import MemoryRetrievalConfig
from ai_company.memory.retriever import ContextInjectionStrategy
from ai_company.memory.shared import SharedKnowledgeStore

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
