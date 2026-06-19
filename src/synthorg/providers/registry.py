"""Provider registry -- the Employment Agency.

Maps provider names to concrete ``BaseCompletionProvider`` driver
instances.  Built from config via ``from_config``, which reads each
provider's ``driver`` field to select the appropriate factory.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Self

from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.provider import (
    PROVIDER_CASSETTE_DRIVER_WRAPPED,
    PROVIDER_DRIVER_FACTORY_MISSING,
    PROVIDER_DRIVER_INSTANTIATED,
    PROVIDER_DRIVER_NOT_REGISTERED,
    PROVIDER_REGISTRY_BUILT,
)

from .base import BaseCompletionProvider
from .cassette import CassetteConfig, CassetteSession
from .errors import (
    DriverFactoryNotFoundError,
    DriverNotRegisteredError,
)

logger = get_logger(__name__)


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
        *,
        cassette_session: CassetteSession | None = None,
    ) -> None:
        """Initialize with a name -> driver mapping.

        Args:
            drivers: Mutable dict of provider name to driver instance.
                The registry takes ownership and freezes a copy.
            cassette_session: The shared cassette session when the
                cassette seam is active, else ``None``. Exposed so an
                app shutdown hook can emit the session-flushed event;
                data durability does not depend on it (the session
                persists after every recorded interaction).
        """
        self._drivers: MappingProxyType[str, BaseCompletionProvider] = MappingProxyType(
            dict(drivers)
        )
        self._cassette_session = cassette_session

    @property
    def cassette_session(self) -> CassetteSession | None:
        """The active cassette session, or ``None`` when inert."""
        return self._cassette_session

    def bind_credential_catalog(self, catalog: ConnectionCatalog | None) -> None:
        """(Re)bind the credential catalog onto every registered driver.

        Boot order can build the provider registry before the always-on
        credential catalog is wired (the catalog needs a connected
        persistence backend). Callers that hold the catalog later (the
        runtime engine assembly) invoke this so every driver resolves
        ``connection_name`` credentials at call time. Idempotent. Drivers
        that do not use catalog-backed credentials inherit a no-op.
        """
        for driver in self._drivers.values():
            driver.bind_credential_catalog(catalog)

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
        """Return sorted tuple of registered provider names.

        Returns:
            A sorted tuple of all registered provider name strings.
        """
        return tuple(sorted(self._drivers))

    def __contains__(self, name: object) -> bool:
        """Check whether a provider name is registered.

        Returns:
            ``True`` if *name* is a registered provider; ``False``
            otherwise.
        """
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
        providers: Mapping[str, ProviderConfig],
        *,
        factory_overrides: dict[str, object] | None = None,
        cassette: CassetteConfig | None = None,
        connection_catalog: ConnectionCatalog | None = None,
    ) -> Self:
        """Build a registry from a provider config dict.

        For each provider, reads the ``driver`` field to select a
        factory.  The factory is called with
        ``(provider_name, config)`` to produce a driver instance, then the
        ``connection_catalog`` (when supplied) is bound onto the driver via
        :meth:`BaseCompletionProvider.bind_credential_catalog` so credentials
        referenced by ``connection_name`` resolve at call time. The catalog
        is the always-on credential catalog (present whenever persistence is
        connected), independent of the integrations feature flag.

        When ``cassette`` is active every driver is wrapped in a
        :class:`CassetteCompletionProvider` sharing one session -- the
        single provider-layer chokepoint, so no consumer (engine,
        coordinator, judge, runtime builder) can bypass record/replay.
        In replay mode the inner driver is **not built at all**: no
        factory is called, so a pure replay run constructs no real
        provider.

        Args:
            providers: Provider config dict (key = provider name).
            factory_overrides: Optional driver-type -> factory
                mapping for testing or native SDK swaps.
            cassette: Cassette configuration; ``None`` or ``off``
                leaves the registry holding the concrete drivers
                unchanged.
            connection_catalog: Always-on credential catalog bound onto
                each built driver for ``connection_name`` resolution;
                ``None`` leaves drivers without catalog-backed credentials.

        Returns:
            A new ``ProviderRegistry`` with all providers registered.

        Raises:
            DriverFactoryNotFoundError: If a provider's ``driver``
                does not match any known factory.
        """
        overrides = factory_overrides or {}

        if cassette is not None and cassette.is_active:
            from .cassette import CassetteMode  # noqa: PLC0415

            if cassette.mode is CassetteMode.REPLAY:
                # Pure replay builds no inner driver, so no concrete
                # driver factory is ever called. Skip importing the
                # driver SDKs entirely: ``litellm`` is an optional
                # dependency a replay-only environment need not have,
                # and importing it here would break the pure-replay
                # contract for no benefit.
                return cls._build_cassette_registry(
                    providers,
                    {},
                    overrides,
                    cassette,
                    connection_catalog,
                )

        from .drivers.litellm_driver import (  # noqa: PLC0415
            LiteLLMDriver,
        )
        from .drivers.scripted import ScriptedDriver  # noqa: PLC0415

        defaults: dict[str, type[BaseCompletionProvider]] = {
            "litellm": LiteLLMDriver,
            "scripted": ScriptedDriver,
        }

        if cassette is not None and cassette.is_active:
            return cls._build_cassette_registry(
                providers,
                defaults,
                overrides,
                cassette,
                connection_catalog,
            )

        drivers: dict[str, BaseCompletionProvider] = {}
        for name, config in providers.items():
            drivers[name] = _build_driver(
                name, config, defaults, overrides, connection_catalog
            )

        logger.info(
            PROVIDER_REGISTRY_BUILT,
            provider_count=len(drivers),
            providers=sorted(drivers),
        )
        return cls(drivers)

    @classmethod
    def _build_cassette_registry(
        cls,
        providers: Mapping[str, ProviderConfig],
        defaults: dict[str, type[BaseCompletionProvider]],
        overrides: dict[str, object],
        cassette: CassetteConfig,
        connection_catalog: ConnectionCatalog | None = None,
    ) -> Self:
        """Wrap every driver in one shared cassette session.

        Replay never builds an inner driver (``inner=None``); record
        builds the real driver and delegates to it.

        Returns:
            A new registry whose drivers are each wrapped in a
            ``CassetteCompletionProvider`` sharing one session.

        Raises:
            DriverFactoryNotFoundError: If the active cassette config has
                no path, or a provider's ``driver`` matches no factory.
        """
        from .cassette import (  # noqa: PLC0415
            CassetteCompletionProvider,
            CassetteMode,
            CassetteSession,
            PatternRedactor,
        )

        if cassette.path is None:  # pragma: no cover - CassetteConfig validates
            msg = "active cassette config must carry a path"
            raise DriverFactoryNotFoundError(msg, context={"cassette": "path"})

        session = CassetteSession(
            mode=cassette.mode,
            path=cassette.path,
            redactor=PatternRedactor(),
        )
        is_replay = cassette.mode is CassetteMode.REPLAY
        drivers: dict[str, BaseCompletionProvider] = {}
        for name, config in providers.items():
            inner = (
                None
                if is_replay
                else _build_driver(
                    name, config, defaults, overrides, connection_catalog
                )
            )
            drivers[name] = CassetteCompletionProvider(
                inner=inner,
                session=session,
                provider_name=name,
            )
            logger.info(
                PROVIDER_CASSETTE_DRIVER_WRAPPED,
                provider=name,
                mode=cassette.mode.value,
            )

        logger.info(
            PROVIDER_REGISTRY_BUILT,
            provider_count=len(drivers),
            providers=sorted(drivers),
        )
        return cls(drivers, cassette_session=session)


def _build_driver(
    name: str,
    config: ProviderConfig,
    defaults: dict[str, type[BaseCompletionProvider]],
    overrides: dict[str, object],
    connection_catalog: ConnectionCatalog | None = None,
) -> BaseCompletionProvider:
    """Instantiate a single driver from config and factories.

    Returns:
        A concrete ``BaseCompletionProvider`` driver instance for the
        named provider.

    Raises:
        DriverFactoryNotFoundError: On unknown driver type or
            non-callable / non-conforming factory.
    """
    driver_type = config.driver
    factory = _resolve_factory(name, driver_type, defaults, overrides)

    try:
        driver = factory(name, config)  # type: ignore[operator]
    except Exception as exc:
        reraise_critical(exc)
        msg = f"Failed to instantiate driver {driver_type!r} for provider {name!r}"
        log_exception_redacted(
            logger,
            PROVIDER_DRIVER_FACTORY_MISSING,
            exc,
            provider=name,
            driver=driver_type,
        )
        raise DriverFactoryNotFoundError(
            msg,
            context={
                "provider": name,
                "driver": driver_type,
                "detail": safe_error_description(exc),
            },
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
    driver.bind_credential_catalog(connection_catalog)
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

    Returns:
        A callable factory for the given ``driver_type``, resolved from
        overrides first, then defaults.

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
