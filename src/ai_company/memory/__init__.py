"""Agent memory system — protocols, models, config, and factory.

Re-exports the protocol, capability protocol, shared knowledge
protocol, config models, factory, and error hierarchy so consumers
can import from ``ai_company.memory`` directly.
"""

from ai_company.memory.capabilities import MemoryCapabilities
from ai_company.memory.config import (
    CompanyMemoryConfig,
    MemoryOptionsConfig,
    MemoryStorageConfig,
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
from ai_company.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from ai_company.memory.protocol import MemoryBackend
from ai_company.memory.shared import SharedKnowledgeStore

__all__ = [
    "CompanyMemoryConfig",
    "MemoryBackend",
    "MemoryCapabilities",
    "MemoryCapabilityError",
    "MemoryConfigError",
    "MemoryConnectionError",
    "MemoryEntry",
    "MemoryError",
    "MemoryMetadata",
    "MemoryNotFoundError",
    "MemoryOptionsConfig",
    "MemoryQuery",
    "MemoryRetrievalError",
    "MemoryStorageConfig",
    "MemoryStoreError",
    "MemoryStoreRequest",
    "SharedKnowledgeStore",
    "create_memory_backend",
]
