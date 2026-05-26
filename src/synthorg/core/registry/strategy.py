"""Generic, immutable registry for protocol+strategy+factory dispatch.

Replaces hand-rolled ``if config.type == "...": ... elif ...`` chains across
pluggable subsystems (pruning, propagation, identity store, proposer, ...).
The pattern is documented in ``docs/reference/pluggable-subsystems.md``.

Each subsystem builds its registry once (typically at module import) from a
mapping of discriminator value to factory callable, then dispatches via
:meth:`StrategyRegistry.build` rather than open-coding the chain.
"""

import enum
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
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


def _key(discriminator: str) -> str:
    """Normalise a discriminator to its plain string form.

    ``StrEnum`` is a ``str`` subclass, so a member is already a valid
    string key; reducing it to ``.value`` strips the enum identity so
    lookups by either ``InterruptType.TOOL_APPROVAL`` or the raw
    ``"tool_approval"`` hit the same factory and logs show the bare
    value. A plain ``str`` is returned unchanged.

    Args:
        discriminator: A string or ``StrEnum`` member (which is a str).

    Returns:
        The canonical string key.
    """
    if isinstance(discriminator, enum.Enum):
        return str(discriminator.value)
    return discriminator


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
        """Freeze *factories* into an immutable view and emit a built event.

        Keys may be plain strings or ``StrEnum`` members; enum keys are
        stored under their ``.value`` so lookups accept either form.
        """
        if not factories:
            msg = f"StrategyRegistry({kind!r}) requires at least one factory"
            raise ValueError(msg)
        self._kind = kind
        self._factories: MappingProxyType[str, Callable[..., T]] = MappingProxyType(
            {_key(k): v for k, v in factories.items()},
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
            name: Discriminator value (e.g. ``"ttl"``, ``"sqlite"``) or
                a ``StrEnum`` member whose ``.value`` is the key.

        Returns:
            The registered factory callable.

        Raises:
            StrategyFactoryNotFoundError: If no factory is registered
                for *name*.
        """
        key = _key(name)
        factory = self._factories.get(key)
        if factory is None:
            available = sorted(self._factories) or ["(none)"]
            logger.error(
                REGISTRY_FACTORY_NOT_FOUND,
                kind=self._kind,
                name=key,
                available=available,
            )
            msg = (
                f"No {self._kind} factory registered for {key!r}. "
                f"Available: {', '.join(available)}"
            )
            raise StrategyFactoryNotFoundError(
                msg,
                context={"kind": self._kind, "name": key},
            )
        return factory

    def build(self, name: str, /, *args: object, **kwargs: object) -> T:
        """Look up *name* and invoke the factory in one step.

        Args:
            name: Discriminator value or ``StrEnum`` member.
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
        key = _key(name)
        factory = self.get(key)
        try:
            instance = factory(*args, **kwargs)
        except Exception as exc:
            reraise_critical(exc)
            # Use ``logger.warning`` + ``safe_error_description``
            # rather than ``logger.exception`` because factory
            # closures used by ``PersistenceBackendRegistry`` may
            # capture credentials in their bound arguments, and
            # ``logger.exception``'s frame-locals capture would
            # surface them in logs.
            logger.warning(
                REGISTRY_FACTORY_FAILED,
                kind=self._kind,
                name=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.debug(
            REGISTRY_FACTORY_INVOKED,
            kind=self._kind,
            name=key,
        )
        return instance

    def names(self) -> tuple[str, ...]:
        """Return registered discriminator values, sorted."""
        return tuple(sorted(self._factories))

    def __contains__(self, name: object) -> bool:
        """Return ``True`` iff *name* is a registered discriminator.

        Accepts a ``str`` or a ``StrEnum`` member (``StrEnum`` is a
        ``str`` subclass); any other type returns ``False`` rather than
        raising so ``x in registry`` stays total.
        """
        if not isinstance(name, str):
            return False
        return _key(name) in self._factories

    def __len__(self) -> int:
        """Return the number of registered factories."""
        return len(self._factories)
