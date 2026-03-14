"""Provider registry — the Employment Agency.

Maps provider names to concrete ``BaseCompletionProvider`` driver
instances.  Built from config via ``from_config``, which reads each
provider's ``driver`` field to select the appropriate factory.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Self

from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_DRIVER_FACTORY_MISSING,
    PROVIDER_DRIVER_INSTANTIATED,
    PROVIDER_DRIVER_NOT_REGISTERED,
    PROVIDER_REGISTRY_BUILT,
)

from .base import BaseCompletionProvider
from .errors import (
    DriverFactoryNotFoundError,
    DriverNotRegisteredError,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from synthorg.config.schema import ProviderConfig


class ProviderRegistry:
    """Immutable registry of named provider drivers.

    Use ``from_config`` to build a registry from a config dict, or
    construct directly with a pre-built mapping.

    Examples:
        Build from config::

            registry = ProviderRegistry.from_config(
                root_config.providers,
            )
            driver = registry.get("example-provider")
            response = await driver.complete(messages, "medium")

        Check membership::

            if "example-provider" in registry:
                ...
    """

    def __init__(
        self,
        drivers: dict[str, BaseCompletionProvider],
    ) -> None:
        """Initialize with a name -> driver mapping.

        Args:
            drivers: Mutable dict of provider name to driver instance.
                The registry takes ownership and freezes a copy.
        """
        self._drivers: MappingProxyType[str, BaseCompletionProvider] = MappingProxyType(
            dict(drivers)
        )

    def get(self, name: str) -> BaseCompletionProvider:
        """Look up a driver by provider name.

        Args:
            name: Provider name (e.g. ``"example-provider"``).

        Returns:
            The registered driver instance.

        Raises:
            DriverNotRegisteredError: If no driver is registered.
        """
        driver = self._drivers.get(name)
        if driver is None:
            available = sorted(self._drivers) or ["(none)"]
            logger.error(
                PROVIDER_DRIVER_NOT_REGISTERED,
                name=name,
                available=available,
            )
            msg = (
                f"Provider {name!r} is not registered. "
                f"Available providers: {', '.join(available)}"
            )
            raise DriverNotRegisteredError(
                msg,
                context={"provider": name},
            )
        return driver

    def list_providers(self) -> tuple[str, ...]:
        """Return sorted tuple of registered provider names."""
        return tuple(sorted(self._drivers))

    def __contains__(self, name: object) -> bool:
        """Check whether a provider name is registered."""
        try:
            return name in self._drivers
        except TypeError:
            return False

    def __len__(self) -> int:
        """Return the number of registered providers."""
        return len(self._drivers)

    @classmethod
    def from_config(
        cls,
        providers: dict[str, ProviderConfig],
        *,
        factory_overrides: dict[str, object] | None = None,
    ) -> Self:
        """Build a registry from a provider config dict.

        For each provider, reads the ``driver`` field to select a
        factory.  The factory is called with
        ``(provider_name, config)`` to produce a driver instance.

        Args:
            providers: Provider config dict (key = provider name).
            factory_overrides: Optional driver-type -> factory
                mapping for testing or native SDK swaps.

        Returns:
            A new ``ProviderRegistry`` with all providers registered.

        Raises:
            DriverFactoryNotFoundError: If a provider's ``driver``
                does not match any known factory.
        """
        from .drivers.litellm_driver import (  # noqa: PLC0415
            LiteLLMDriver,
        )

        defaults: dict[str, type[BaseCompletionProvider]] = {
            "litellm": LiteLLMDriver,
        }
        overrides = factory_overrides or {}
        drivers: dict[str, BaseCompletionProvider] = {}

        for name, config in providers.items():
            driver = _build_driver(
                name,
                config,
                defaults,
                overrides,
            )
            drivers[name] = driver

        logger.info(
            PROVIDER_REGISTRY_BUILT,
            provider_count=len(drivers),
            providers=sorted(drivers),
        )
        return cls(drivers)


def _build_driver(
    name: str,
    config: ProviderConfig,
    defaults: dict[str, type[BaseCompletionProvider]],
    overrides: dict[str, object],
) -> BaseCompletionProvider:
    """Instantiate a single driver from config and factories.

    Raises:
        DriverFactoryNotFoundError: On unknown driver type or
            non-callable / non-conforming factory.
    """
    driver_type = config.driver
    factory = _resolve_factory(name, driver_type, defaults, overrides)

    try:
        driver = factory(name, config)  # type: ignore[operator]
    except Exception as exc:
        msg = f"Failed to instantiate driver {driver_type!r} for provider {name!r}"
        logger.exception(
            PROVIDER_DRIVER_FACTORY_MISSING,
            provider=name,
            driver=driver_type,
        )
        raise DriverFactoryNotFoundError(
            msg,
            context={"provider": name, "driver": driver_type, "detail": str(exc)},
        ) from exc
    if not isinstance(driver, BaseCompletionProvider):
        msg = (
            f"Factory for {driver_type!r} did not produce a "
            f"BaseCompletionProvider instance"
        )
        logger.error(
            PROVIDER_DRIVER_FACTORY_MISSING,
            provider=name,
            driver=driver_type,
            error="factory returned non-BaseCompletionProvider",
        )
        raise DriverFactoryNotFoundError(
            msg,
            context={"provider": name, "driver": driver_type},
        )
    logger.debug(
        PROVIDER_DRIVER_INSTANTIATED,
        provider=name,
        driver=driver_type,
    )
    return driver


def _resolve_factory(
    name: str,
    driver_type: str,
    defaults: dict[str, type[BaseCompletionProvider]],
    overrides: dict[str, object],
) -> object:
    """Look up and validate a callable factory for the driver type.

    Raises:
        DriverFactoryNotFoundError: If no factory found or not callable.
    """
    factory: object | None = overrides.get(driver_type)
    if factory is None:
        factory = defaults.get(driver_type)

    if factory is None:
        available = sorted(set(defaults) | set(overrides))
        logger.error(
            PROVIDER_DRIVER_FACTORY_MISSING,
            provider=name,
            driver=driver_type,
            available=available,
        )
        msg = (
            f"No factory for driver type {driver_type!r} "
            f"(provider {name!r}). Available: {available}"
        )
        raise DriverFactoryNotFoundError(
            msg,
            context={"provider": name, "driver": driver_type},
        )

    if not callable(factory):
        msg = f"Factory for driver {driver_type!r} is not callable"
        logger.error(
            PROVIDER_DRIVER_FACTORY_MISSING,
            provider=name,
            driver=driver_type,
            error="factory is not callable",
        )
        raise DriverFactoryNotFoundError(
            msg,
            context={"provider": name, "driver": driver_type},
        )
    return factory
