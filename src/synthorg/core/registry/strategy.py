"""Generic, immutable registry for protocol+strategy+factory dispatch.

Replaces hand-rolled ``if config.type == "...": ... elif ...`` chains across
pluggable subsystems (pruning, propagation, identity store, proposer, ...).
The pattern is documented in ``docs/reference/pluggable-subsystems.md``.

Each subsystem builds its registry once (typically at module import) from a
mapping of discriminator value to factory callable, then dispatches via
:meth:`StrategyRegistry.build` rather than open-coding the chain.
"""

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
    from collections.abc import Callable, Mapping

logger = get_logger(__name__)


class StrategyRegistry[T]:
    """Immutable registry mapping discriminator strings to factory callables.

    Examples:
        Build and dispatch::

            registry = StrategyRegistry[Greeter](
                {"loud": LoudGreeter, "quiet": QuietGreeter},
                kind="greeter",
            )
            greeter = registry.build("loud", name="Daisy")

        Membership and enumeration::

            "loud" in registry
            registry.names()  # ("loud", "quiet")

    Args:
        factories: Mapping of discriminator value to factory callable.
            The factory must produce an instance of ``T`` when invoked
            with the build-time arguments.
        kind: Short identifier of the subsystem (e.g. ``"pruning"``,
            ``"propagation"``). Surfaces in error messages and log
            events so multiple registries are distinguishable.

    Raises:
        ValueError: If *factories* is empty (a registry with no
            entries can never satisfy a lookup).
    """

    def __init__(
        self,
        factories: Mapping[str, Callable[..., T]],
        *,
        kind: str,
    ) -> None:
        """Freeze *factories* into an immutable view and emit a built event."""
        if not factories:
            msg = f"StrategyRegistry({kind!r}) requires at least one factory"
            raise ValueError(msg)
        self._kind = kind
        self._factories: MappingProxyType[str, Callable[..., T]] = MappingProxyType(
            dict(factories),
        )
        logger.info(
            REGISTRY_BUILT,
            kind=kind,
            count=len(self._factories),
            names=sorted(self._factories),
        )

    @property
    def kind(self) -> str:
        """Subsystem identifier supplied at construction."""
        return self._kind

    def get(self, name: str) -> Callable[..., T]:
        """Look up a factory by discriminator value.

        Args:
            name: Discriminator value (e.g. ``"ttl"``, ``"sqlite"``).

        Returns:
            The registered factory callable.

        Raises:
            StrategyFactoryNotFoundError: If no factory is registered
                for *name*.
        """
        factory = self._factories.get(name)
        if factory is None:
            available = sorted(self._factories) or ["(none)"]
            logger.error(
                REGISTRY_FACTORY_NOT_FOUND,
                kind=self._kind,
                name=name,
                available=available,
            )
            msg = (
                f"No {self._kind} factory registered for {name!r}. "
                f"Available: {', '.join(available)}"
            )
            raise StrategyFactoryNotFoundError(
                msg,
                context={"kind": self._kind, "name": name},
            )
        return factory

    def build(self, name: str, /, *args: object, **kwargs: object) -> T:
        """Look up *name* and invoke the factory in one step.

        Args:
            name: Discriminator value.
            *args: Positional arguments forwarded to the factory.
            **kwargs: Keyword arguments forwarded to the factory.

        Returns:
            The instance produced by the factory.

        Raises:
            StrategyFactoryNotFoundError: If no factory is registered.
            Exception: Any exception raised by the factory propagates
                untouched after a structured ``REGISTRY_FACTORY_FAILED``
                log event is emitted.
        """
        factory = self.get(name)
        try:
            instance = factory(*args, **kwargs)
        except Exception as exc:
            # Use ``logger.warning`` + ``safe_error_description``
            # rather than ``logger.exception`` because factory
            # closures used by ``PersistenceBackendRegistry`` may
            # capture credentials in their bound arguments, and
            # ``logger.exception``'s frame-locals capture would
            # surface them in logs.
            logger.warning(
                REGISTRY_FACTORY_FAILED,
                kind=self._kind,
                name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.debug(
            REGISTRY_FACTORY_INVOKED,
            kind=self._kind,
            name=name,
        )
        return instance

    def names(self) -> tuple[str, ...]:
        """Return registered discriminator values, sorted."""
        return tuple(sorted(self._factories))

    def __contains__(self, name: object) -> bool:
        """Return ``True`` iff *name* is a registered string discriminator."""
        if not isinstance(name, str):
            return False
        return name in self._factories

    def __len__(self) -> int:
        """Return the number of registered factories."""
        return len(self._factories)
