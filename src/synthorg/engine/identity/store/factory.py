"""Factory for building identity version stores from config."""

from synthorg.core.agent import AgentIdentity
from synthorg.core.registry import StrategyRegistry
from synthorg.engine.identity.store.append_only import AppendOnlyIdentityStore
from synthorg.engine.identity.store.config import IdentityStoreConfig
from synthorg.engine.identity.store.copy_on_write import CopyOnWriteIdentityStore
from synthorg.engine.identity.store.protocol import IdentityVersionStore
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger
from synthorg.versioning.service import VersioningService

logger = get_logger(__name__)


def _build_append_only(
    _config: IdentityStoreConfig,
    *,
    registry: AgentRegistryService,
    versioning: VersioningService[AgentIdentity],
) -> IdentityVersionStore:
    return AppendOnlyIdentityStore(registry=registry, versioning=versioning)


def _build_copy_on_write(
    _config: IdentityStoreConfig,
    *,
    registry: AgentRegistryService,
    versioning: VersioningService[AgentIdentity],
) -> IdentityVersionStore:
    return CopyOnWriteIdentityStore(registry=registry, versioning=versioning)


_REGISTRY: StrategyRegistry[IdentityVersionStore] = StrategyRegistry(
    {
        "append_only": _build_append_only,
        "copy_on_write": _build_copy_on_write,
    },
    kind="identity_store",
)


def build_identity_store(
    config: IdentityStoreConfig,
    *,
    registry: AgentRegistryService,
    versioning: VersioningService[AgentIdentity],
) -> IdentityVersionStore:
    """Build an identity version store from configuration.

    Args:
        config: Identity store configuration.
        registry: Agent registry service.
        versioning: Versioning service for AgentIdentity.

    Returns:
        Configured identity version store.

    Raises:
        StrategyFactoryNotFoundError: If ``config.type`` is not registered.
    """
    return _REGISTRY.build(
        config.type,
        config,
        registry=registry,
        versioning=versioning,
    )
