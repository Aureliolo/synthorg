"""Shared organizational memory — protocols, models, config, and factory.

Re-exports the public API so consumers can import from
``synthorg.memory.org`` directly.
"""

from synthorg.memory.org.access_control import (
    CategoryWriteRule,
    WriteAccessConfig,
    check_write_access,
    require_write_access,
)
from synthorg.memory.org.config import ExtendedStoreConfig, OrgMemoryConfig
from synthorg.memory.org.errors import (
    OrgMemoryAccessDeniedError,
    OrgMemoryConfigError,
    OrgMemoryConnectionError,
    OrgMemoryError,
    OrgMemoryQueryError,
    OrgMemoryWriteError,
)
from synthorg.memory.org.factory import create_org_memory_backend
from synthorg.memory.org.hybrid_backend import HybridPromptRetrievalBackend
from synthorg.memory.org.models import (
    OrgFact,
    OrgFactAuthor,
    OrgFactWriteRequest,
    OrgMemoryQuery,
)
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.org.store import OrgFactStore, SQLiteOrgFactStore

__all__ = [
    "CategoryWriteRule",
    "ExtendedStoreConfig",
    "HybridPromptRetrievalBackend",
    "OrgFact",
    "OrgFactAuthor",
    "OrgFactStore",
    "OrgFactWriteRequest",
    "OrgMemoryAccessDeniedError",
    "OrgMemoryBackend",
    "OrgMemoryConfig",
    "OrgMemoryConfigError",
    "OrgMemoryConnectionError",
    "OrgMemoryError",
    "OrgMemoryQuery",
    "OrgMemoryQueryError",
    "OrgMemoryWriteError",
    "SQLiteOrgFactStore",
    "WriteAccessConfig",
    "check_write_access",
    "create_org_memory_backend",
    "require_write_access",
]
