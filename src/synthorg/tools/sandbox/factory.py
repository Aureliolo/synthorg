"""Sandbox backend factory -- build and resolve backends from config.

Provides ``build_sandbox_backends`` to instantiate only the backends
referenced by a ``SandboxingConfig``, ``resolve_sandbox_for_category``
to look up the correct backend for a tool category, and
``cleanup_sandbox_backends`` to release resources.
"""

import asyncio
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.registry import StrategyRegistry
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.sandbox import (
    SANDBOX_FACTORY_BUILD_FAILED,
    SANDBOX_FACTORY_BUILT,
    SANDBOX_FACTORY_CLEANUP,
    SANDBOX_FACTORY_CLEANUP_FAILED,
    SANDBOX_FACTORY_RESOLVE,
    SANDBOX_FACTORY_RESOLVE_FAILED,
)
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.sandbox.subprocess_sandbox import SubprocessSandbox

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from synthorg.core.enums import ToolCategory
    from synthorg.persistence.tracked_container_protocol import (
        TrackedContainerRepository,
    )
    from synthorg.tools.sandbox.lifecycle.protocol import (
        SandboxLifecycleStrategy,
    )
    from synthorg.tools.sandbox.protocol import SandboxBackend
    from synthorg.tools.sandbox.sandboxing_config import SandboxingConfig

logger = get_logger(__name__)

# Default gVisor overrides for high-risk tool categories.
# User-supplied runtime_overrides take precedence.
_DEFAULT_GVISOR_OVERRIDES: MappingProxyType[str, str] = MappingProxyType(
    {
        "code_execution": "runsc",
        "terminal": "runsc",
    }
)


def _build_subprocess_backend(
    *,
    config: SandboxingConfig,
    workspace: Path,
    tracked_container_repo: TrackedContainerRepository | None = None,
    lifecycle_strategy: SandboxLifecycleStrategy | None = None,
) -> SandboxBackend:
    # Subprocess backend has no Docker containers to track or reuse;
    # the repo / lifecycle parameters are accepted to keep a uniform
    # registry signature.
    del tracked_container_repo, lifecycle_strategy
    try:
        return SubprocessSandbox(
            config=config.subprocess,
            workspace=workspace,
        )
    except Exception as exc:
        log_exception_redacted(
            logger,
            SANDBOX_FACTORY_BUILD_FAILED,
            exc,
            backend="subprocess",
            workspace=str(workspace),
        )
        raise


def _build_docker_backend(
    *,
    config: SandboxingConfig,
    workspace: Path,
    tracked_container_repo: TrackedContainerRepository | None = None,
    lifecycle_strategy: SandboxLifecycleStrategy | None = None,
) -> SandboxBackend:
    try:
        return DockerSandbox(
            config=config.docker,
            workspace=workspace,
            tracked_container_repo=tracked_container_repo,
            lifecycle_strategy=lifecycle_strategy,
        )
    except Exception as exc:
        log_exception_redacted(
            logger,
            SANDBOX_FACTORY_BUILD_FAILED,
            exc,
            backend="docker",
            workspace=str(workspace),
        )
        raise


_SANDBOX_BACKEND_REGISTRY: StrategyRegistry[SandboxBackend] = StrategyRegistry(
    {
        "subprocess": _build_subprocess_backend,
        "docker": _build_docker_backend,
    },
    kind="sandbox_backend",
)

_KNOWN_BACKENDS: frozenset[str] = frozenset(_SANDBOX_BACKEND_REGISTRY.names())


def build_sandbox_backends(
    *,
    config: SandboxingConfig,
    workspace: Path,
    tracked_container_repo: TrackedContainerRepository | None = None,
    lifecycle_strategy: SandboxLifecycleStrategy | None = None,
) -> MappingProxyType[str, SandboxBackend]:
    """Build only the backend instances actually referenced by *config*.

    Collects which backend names are needed (the default plus all
    override values), then instantiates ``SubprocessSandbox`` and/or
    ``DockerSandbox`` with their respective sub-configs.

    Args:
        config: Top-level sandboxing configuration.
        workspace: Absolute path to the agent workspace root.
        tracked_container_repo: Optional persistence handle wired into
            the Docker backend so container tracking survives restart.
            Subprocess backend ignores it.
        lifecycle_strategy: Optional container lifecycle strategy
            injected into the Docker backend (per-agent / per-task /
            per-call).  When ``None`` the Docker backend defaults to
            per-call.  Subprocess backend ignores it.

    Returns:
        A read-only mapping of backend name to backend instance.
        Only contains keys for backends that are actually referenced.

    Raises:
        ValueError: If *config* references an unrecognized backend
            name not in the known set (``subprocess``, ``docker``).
    """
    needed: set[str] = {config.default_backend}
    needed.update(config.overrides.values())

    unknown = needed - _KNOWN_BACKENDS
    if unknown:
        msg = (
            f"Unrecognized sandbox backend name(s): {sorted(unknown)}; "
            f"known backends: {sorted(_KNOWN_BACKENDS)}"
        )
        logger.error(SANDBOX_FACTORY_BUILD_FAILED, error=msg)
        raise ValueError(msg)

    backends: dict[str, SandboxBackend] = {
        name: _SANDBOX_BACKEND_REGISTRY.build(
            name,
            config=config,
            workspace=workspace,
            tracked_container_repo=tracked_container_repo,
            lifecycle_strategy=lifecycle_strategy,
        )
        for name in sorted(needed)
    }

    logger.info(
        SANDBOX_FACTORY_BUILT,
        backends=sorted(backends.keys()),
        default=config.default_backend,
        override_count=len(config.overrides),
    )
    return MappingProxyType(backends)


def merge_gvisor_defaults(
    config: SandboxingConfig,
) -> SandboxingConfig:
    """Return a new config with default gVisor runtime overrides merged.

    User-supplied ``runtime_overrides`` take precedence over defaults.
    Only merges when the Docker backend is referenced by the config.

    Args:
        config: Original sandboxing configuration.

    Returns:
        A new ``SandboxingConfig`` with merged runtime overrides
        on the Docker sub-config, or the original config unchanged
        if Docker is not referenced.
    """
    needed: set[str] = {config.default_backend}
    needed.update(config.overrides.values())
    if "docker" not in needed:
        return config

    effective_overrides = {
        **_DEFAULT_GVISOR_OVERRIDES,
        **config.docker.runtime_overrides,
    }
    if effective_overrides == dict(config.docker.runtime_overrides):
        return config

    new_docker = config.docker.model_copy(
        update={"runtime_overrides": effective_overrides},
    )
    return config.model_copy(update={"docker": new_docker})


def resolve_sandbox_for_category(
    *,
    config: SandboxingConfig,
    backends: Mapping[str, SandboxBackend],
    category: ToolCategory,
) -> SandboxBackend:
    """Look up the correct backend for a tool category.

    Uses ``config.backend_for_category()`` to determine the backend
    name, then returns the corresponding instance from *backends*.

    Args:
        config: Top-level sandboxing configuration.
        backends: Mapping of backend name to backend instance.
        category: The tool category to resolve.

    Returns:
        The ``SandboxBackend`` instance for the given category.

    Raises:
        KeyError: If the resolved backend name is not present in
            *backends*.
    """
    backend_name = config.backend_for_category(category.value)
    try:
        backend = backends[backend_name]
    except KeyError as exc:
        msg = (
            f"Backend {backend_name!r} resolved for category "
            f"{category.value!r} not found in backends mapping "
            f"(available: {sorted(backends.keys())})"
        )
        logger.warning(
            SANDBOX_FACTORY_RESOLVE_FAILED,
            category=category.value,
            backend=backend_name,
            error=msg,
        )
        raise KeyError(msg) from exc

    logger.debug(
        SANDBOX_FACTORY_RESOLVE,
        category=category.value,
        backend=backend_name,
    )
    return backend


async def cleanup_sandbox_backends(
    *,
    backends: Mapping[str, SandboxBackend],
) -> None:
    """Clean up all backends by calling ``cleanup()`` on each.

    Errors from individual backends are logged but do not prevent
    cleanup of remaining backends.  Uses ``asyncio.gather`` with
    ``return_exceptions=True`` for best-effort parallel cleanup
    that is resilient to task cancellation.

    Args:
        backends: Mapping of backend name to backend instance.
    """

    async def _cleanup_one(name: str, backend: SandboxBackend) -> None:
        logger.debug(SANDBOX_FACTORY_CLEANUP, backend=name)
        try:
            await backend.cleanup()
        except Exception:
            logger.warning(
                SANDBOX_FACTORY_CLEANUP_FAILED,
                backend=name,
                error=f"cleanup failed for backend {name!r}",
            )

    # NOTE: intentionally using gather(return_exceptions=True) instead of
    # TaskGroup here.  TaskGroup cancels all siblings when one task raises
    # a BaseException (e.g. CancelledError during shutdown), defeating
    # the error-isolation guarantee this function promises.  gather keeps
    # all tasks running independently.
    backend_items = list(backends.items())
    results = await asyncio.gather(
        *(_cleanup_one(n, b) for n, b in backend_items),
        return_exceptions=True,
    )
    # Log BaseException subclasses (CancelledError, KeyboardInterrupt)
    # that escaped _cleanup_one's except Exception block
    for (name, _), result in zip(backend_items, results, strict=True):
        if isinstance(result, BaseException):
            log_exception_redacted(
                logger,
                SANDBOX_FACTORY_CLEANUP_FAILED,
                result,
                backend=name,
                reason="unhandled_exception_during_cleanup",
            )
