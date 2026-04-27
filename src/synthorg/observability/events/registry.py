"""Registry event constants for structured logging.

Used by ``synthorg.core.registry``, ``synthorg.persistence.registry``, and
``synthorg.memory.registry`` to emit consistent structured-logging events
during construction and lookup.
"""

from typing import Final

REGISTRY_BUILT: Final[str] = "registry.built"
REGISTRY_FACTORY_NOT_FOUND: Final[str] = "registry.factory.not_found"
REGISTRY_FACTORY_INVOKED: Final[str] = "registry.factory.invoked"
REGISTRY_FACTORY_FAILED: Final[str] = "registry.factory.failed"
