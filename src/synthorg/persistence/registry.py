"""Persistence backend registry.

Domain-specific dispatch keyed by ``PersistenceConfig.backend``.  Replaces
the hand-rolled ``if backend == "sqlite": ... elif "postgres": ...`` chain
in ``synthorg.persistence.factory`` while preserving the lazy import of the
optional ``synthorg.persistence.postgres`` extra.
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
    from synthorg.persistence.config import PersistenceConfig
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


type _PersistenceFactory = Callable[["PersistenceConfig"], "PersistenceBackend"]


class PersistenceBackendRegistry:
    """Immutable registry mapping backend names to persistence-backend factories.

    Each factory accepts a ``PersistenceConfig`` and returns a
    disconnected :class:`synthorg.persistence.protocol.PersistenceBackend`.
    Lazy imports inside the factory closures are encouraged for optional
    dependencies (currently the ``postgres`` extra).
    """

    _KIND = "persistence_backend"

    def __init__(self, factories: Mapping[str, _PersistenceFactory]) -> None:
        """Freeze *factories* and emit a registry-built event.

        Raises:
            ValueError: If an argument fails validation.
        """
        if not factories:
            msg = "PersistenceBackendRegistry requires at least one factory"
            raise ValueError(msg)
        self._factories: MappingProxyType[str, _PersistenceFactory] = MappingProxyType(
            dict(factories),
        )
        logger.info(
            REGISTRY_BUILT,
            kind=self._KIND,
            count=len(self._factories),
            names=sorted(self._factories),
        )

    def build(self, config: PersistenceConfig) -> PersistenceBackend:
        """Dispatch to the factory registered for ``config.backend``.

        Args:
            config: Persistence configuration with the backend
                discriminator and per-backend nested config.

        Returns:
            A new, disconnected backend instance.

        Raises:
            StrategyFactoryNotFoundError: If ``config.backend`` is not
                registered.
            Exception: Any exception raised by the factory propagates
                untouched (e.g. ``PersistenceConnectionError`` when the
                postgres extra is missing).
        """
        name = config.backend
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
                f"No persistence_backend factory registered for {name!r}. "
                f"Available: {', '.join(available)}"
            )
            raise StrategyFactoryNotFoundError(
                msg,
                context={"kind": self._KIND, "name": name},
            )
        try:
            backend = factory(config)
        except Exception as exc:
            # Persistence factories take a ``PersistenceConfig``
            # whose ``PostgresConfig`` carries ``SecretStr``
            # credentials. Use ``logger.warning`` +
            # ``safe_error_description`` so frame-locals are not
            # captured in logs.
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
            The matching collection.
        """
        return tuple(sorted(self._factories))

    def __contains__(self, name: object) -> bool:
        """Return ``True`` iff *name* is a registered string discriminator.

        Returns:
            ``True`` when ``name`` is a registered discriminator, ``False`` otherwise.
        """
        if not isinstance(name, str):
            return False
        return name in self._factories

    def __len__(self) -> int:
        """Number of registered backends.

        Returns:
            Numeric result of the operation.
        """
        return len(self._factories)
