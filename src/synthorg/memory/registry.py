"""Memory backend registry.

Domain-specific dispatch keyed by ``CompanyMemoryConfig.backend``.  Mirrors
``PersistenceBackendRegistry`` and replaces the hand-rolled
``if backend == "...": ... elif ...`` chain previously embedded in
``synthorg.memory.factory`` and ``CompositeBackend`` child wiring.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.observability import get_logger
from synthorg.observability.events.registry import (
    REGISTRY_BUILT,
    REGISTRY_FACTORY_FAILED,
    REGISTRY_FACTORY_INVOKED,
    REGISTRY_FACTORY_NOT_FOUND,
)
from synthorg.observability.redaction import safe_error_description

if TYPE_CHECKING:
    from synthorg.memory.backends.mem0.config import Mem0EmbedderConfig
    from synthorg.memory.config import CompanyMemoryConfig
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)


type _MemoryFactory = Callable[..., "MemoryBackend"]


class MemoryBackendRegistry:
    """Immutable registry mapping backend names to memory-backend factories.

    Each factory accepts a ``CompanyMemoryConfig`` and an optional
    ``Mem0EmbedderConfig`` (kwarg ``embedder``) and returns a
    disconnected :class:`synthorg.memory.protocol.MemoryBackend`.
    """

    _KIND = "memory_backend"

    def __init__(self, factories: Mapping[str, _MemoryFactory]) -> None:
        """Freeze *factories* and emit a registry-built event.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if not factories:
            msg = "MemoryBackendRegistry requires at least one factory"
            raise ValueError(msg)
        self._factories: MappingProxyType[str, _MemoryFactory] = MappingProxyType(
            dict(factories),
        )
        logger.info(
            REGISTRY_BUILT,
            kind=self._KIND,
            count=len(self._factories),
            names=sorted(self._factories),
        )

    def build(
        self,
        name: str,
        config: CompanyMemoryConfig,
        *,
        embedder: Mem0EmbedderConfig | None = None,
    ) -> MemoryBackend:
        """Dispatch to the factory registered for *name*.

        Args:
            name: Backend discriminator (e.g. ``"mem0"``, ``"inmemory"``,
                ``"composite"``).
            config: Company memory configuration.
            embedder: Optional embedder config forwarded to factories
                that need it (currently only ``mem0`` and the composite
                wrapper for its mem0 children).

        Returns:
            A new, disconnected backend instance.

        Raises:
            StrategyFactoryNotFoundError: If *name* is not registered.
            Exception: Raised when the relevant invariant fails.
        """
        factory = self._factories.get(name)
        if factory is None:
            available = sorted(self._factories) or ["(none)"]
            logger.error(
                REGISTRY_FACTORY_NOT_FOUND,
                kind=self._KIND,
                name=name,
                available=available,
            )
            msg = (
                f"No memory_backend factory registered for {name!r}. "
                f"Available: {', '.join(available)}"
            )
            raise StrategyFactoryNotFoundError(
                msg,
                context={"kind": self._KIND, "name": name},
            )
        try:
            backend = factory(config, embedder=embedder)
        except Exception as exc:
            # Memory factories may close over an embedder containing
            # API credentials. Use ``logger.warning`` +
            # ``safe_error_description`` to avoid frame-local capture
            # in logs.
            logger.warning(
                REGISTRY_FACTORY_FAILED,
                kind=self._KIND,
                name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.debug(
            REGISTRY_FACTORY_INVOKED,
            kind=self._KIND,
            name=name,
        )
        return backend

    def names(self) -> tuple[str, ...]:
        """Sorted tuple of registered backend names.

        Returns:
            Tuple of ``str``.
        """
        return tuple(sorted(self._factories))

    def __contains__(self, name: object) -> bool:
        """Return ``True`` iff *name* is a registered string discriminator.

        Returns:
            ``True`` if the operation succeeds, ``False`` otherwise.
        """
        if not isinstance(name, str):
            return False
        return name in self._factories

    def __len__(self) -> int:
        """Number of registered backends.

        Returns:
            Result of type ``int``.
        """
        return len(self._factories)
