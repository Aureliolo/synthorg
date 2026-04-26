"""Test-only synchronous reset hooks for :mod:`synthorg.hr.registry`.

Production code MUST use ``await registry.clear()`` -- the async path
holds the registry's internal lock and is the only safe public reset
contract. This module exposes a deliberately separate sync helper for
pytest fixtures whose harness is sync-only; locating it here (rather
than as a method on :class:`AgentRegistryService`) keeps the production
class surface free of an unsafe lock-bypass that an unsuspecting
caller might use by autocomplete.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_REGISTRY_CLEARED

if TYPE_CHECKING:
    from synthorg.hr.registry import AgentRegistryService

logger = get_logger(__name__)


def reset_registry_for_test_sync(registry: AgentRegistryService) -> None:
    """Reset the registry's in-memory state synchronously.

    Bypasses ``registry._lock`` -- callers MUST guarantee no async
    operations are in flight against this registry. Production code
    must call ``await registry.clear()`` instead; this helper exists
    solely to support sync pytest fixtures that cannot use an async
    teardown hook.
    """
    cleared_count = len(registry._agents)  # noqa: SLF001
    registry._agents.clear()  # noqa: SLF001
    logger.info(HR_REGISTRY_CLEARED, cleared_count=cleared_count)
