"""Registry event constants for structured logging.

Used by ``synthorg.core.registry``, ``synthorg.persistence.registry``,
``synthorg.memory.registry``, and ``synthorg.core.feature_map`` to emit
consistent structured-logging events during construction and lookup.
"""

from typing import Final

REGISTRY_BUILT: Final[str] = "registry.built"
REGISTRY_FEATURE_IMPORT_FAILED: Final[str] = "registry.feature.import_failed"
REGISTRY_FACTORY_NOT_FOUND: Final[str] = "registry.factory.not_found"
REGISTRY_FACTORY_INVOKED: Final[str] = "registry.factory.invoked"
REGISTRY_FACTORY_FAILED: Final[str] = "registry.factory.failed"
