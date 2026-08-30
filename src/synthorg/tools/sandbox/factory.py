"""Sandbox backend factory -- build and resolve backends from config.

Provides ``build_sandbox_backends`` to instantiate only the backends
referenced by a ``SandboxingConfig``, ``resolve_sandbox_for_category``
to look up the correct backend for a tool category, and
``cleanup_sandbox_backends`` to release resources.
"""

import asyncio
import weakref
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from synthorg.core.critical_errors import reraise_critical
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
from synthorg.persistence.tracked_container_protocol import (
    TrackedContainerRepository,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.sandbox._image_resolution import (
    get_resolved_sandbox_image,
    get_resolved_sidecar_image,
)
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.sandbox.lifecycle.protocol import (
    SandboxLifecycleStrategy,
)
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.sandboxing_config import (
    UNTRUSTED_EXEC_CATEGORIES,
    SandboxingConfig,
)
from synthorg.tools.sandbox.subprocess_sandbox import SubprocessSandbox

logger = get_logger(__name__)

# Default gVisor overrides for high-risk tool categories.
# User-supplied runtime_overrides take precedence.
_DEFAULT_GVISOR_OVERRIDES: MappingProxyType[str, str] = MappingProxyType(
    {
        "code_execution": "runsc",
        "terminal": "runsc",
    }
)

# Tool categories that execute untrusted, agent-authored code. These must
# run inside a container, never the API process, so they take the Docker
# backend even when the global default is ``subprocess`` (which is acceptable
# for low-risk read-only tools). This also makes the gVisor runtime overrides
# above coherent: they only apply under the Docker backend. An operator
# override to ``subprocess`` is refused by ``SandboxingConfig`` itself rather
# than winning here, so this set and that refusal cannot disagree.
_UNTRUSTED_EXEC_CATEGORIES: frozenset[str] = UNTRUSTED_EXEC_CATEGORIES


def _build_subprocess_backend(
    *,
    config: SandboxingConfig,
    workspace: Path,
    tracked_container_repo: TrackedContainerRepository | None = None,
    lifecycle_strategy: SandboxLifecycleStrategy | None = None,
    background_jobs: BackgroundJobRegistry | None = None,
) -> SandboxBackend:
    # Subprocess backend has no Docker containers to track or reuse;
    # the repo / lifecycle / background-job parameters are accepted to
    # keep a uniform registry signature.
    """Build subprocess backend.

    Returns:
        Result of type ``SandboxBackend``.

    Raises:
        Exception: Raised when the relevant invariant fails.
    """
    del tracked_container_repo, lifecycle_strategy, background_jobs
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


def _with_resolved_images(config: DockerSandboxConfig) -> DockerSandboxConfig:
    """Re-read the image fields nobody set explicitly from the live cache.

    The two image fields default from a resolution cache that startup fills
    from ``tools.sandbox_image`` / ``tools.sidecar_image``, and so from the
    digest the CLI pinned into the compose file. But the config itself is part
    of ``RootConfig``, built while the app is constructed and long before that
    cache exists, so its defaults froze the version-tag fallback and every
    sandbox ran on it for the life of the process. On a dev build that tag is
    one the registry does not carry, so every ``shell_command`` and
    ``code_runner`` call answered "No such image", no ``CodeExecutionRecord``
    could be minted, and the build/test oracle had nothing to read.

    ``model_fields_set`` is what keeps this from overriding an operator: a
    field named in YAML is in it, one that came from the default factory is
    not.

    Returns:
        The config, with any defaulted image re-resolved.
    """
    defaulted = {
        field: value
        for field, value in (
            ("image", get_resolved_sandbox_image()),
            ("sidecar_image", get_resolved_sidecar_image()),
        )
        if field not in config.model_fields_set
    }
    return config.model_copy(update=defaulted) if defaulted else config


def _build_docker_backend(
    *,
    config: SandboxingConfig,
    workspace: Path,
    tracked_container_repo: TrackedContainerRepository | None = None,
    lifecycle_strategy: SandboxLifecycleStrategy | None = None,
    background_jobs: BackgroundJobRegistry | None = None,
) -> SandboxBackend:
    """Build docker backend.

    Returns:
        Result of type ``SandboxBackend``.

    Raises:
        Exception: Raised when the relevant invariant fails.
    """
    try:
        return DockerSandbox(
            config=_with_resolved_images(config.docker),
            workspace=workspace,
            tracked_container_repo=tracked_container_repo,
            lifecycle_strategy=lifecycle_strategy,
            background_jobs=background_jobs,
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

#: Every backend this process built, against the name it was built under.
#:
#: DERIVED rather than listed, because four call sites build backends
#: independently and no one of them sees the others: the agent runtime, the
#: tool factory when it is handed none, the toolsmith wiring and the
#: self-improvement code applier. Nothing memoises, so a running deployment
#: holds two or three separate container backends, each with its own warm
#: container pool. A shutdown draining whichever mapping one owner happened to
#: keep would reclaim a subset and report it as the whole, and a fifth call
#: site added later would silently not be covered at all.
#:
#: Weakly keyed, so tracking never keeps a backend alive past the owner that
#: built it. A backend already collected is one whose containers its own
#: lifecycle strategy released on the way out; holding it here to tear down
#: later would turn a teardown into a leak of its own.
_BUILT_BACKENDS: weakref.WeakKeyDictionary[SandboxBackend, str] = (
    weakref.WeakKeyDictionary()
)


def build_sandbox_backends(
    *,
    config: SandboxingConfig,
    workspace: Path,
    tracked_container_repo: TrackedContainerRepository | None = None,
    lifecycle_strategy: SandboxLifecycleStrategy | None = None,
    background_jobs: BackgroundJobRegistry | None = None,
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
        background_jobs: Optional background-job registry injected into
            the Docker backend.  ``None`` disables ``start_background``
            / ``poll_background`` / ``read_background_output`` /
            ``cancel_background`` (each refuses loudly rather than
            silently no-opping).  Subprocess backend ignores it.

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
            background_jobs=background_jobs,
        )
        for name in sorted(needed)
    }
    for name, backend in backends.items():
        _BUILT_BACKENDS[backend] = name

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


def merge_secure_backend_defaults(
    config: SandboxingConfig,
) -> SandboxingConfig:
    """Force untrusted-exec categories onto the container backend.

    Returns a new config whose ``overrides`` route the
    ``_UNTRUSTED_EXEC_CATEGORIES`` (agent-authored code execution) to the
    ``docker`` backend, so that code is never run in the API process even
    when ``default_backend`` is ``subprocess``. Operator-supplied
    per-category overrides win. Returns the original config unchanged
    when no change is needed.

    Args:
        config: Original sandboxing configuration.

    Returns:
        A new ``SandboxingConfig`` with secure backend defaults merged,
        or the original config when nothing changed.
    """
    merged = dict(config.overrides)
    changed = False
    for category in _UNTRUSTED_EXEC_CATEGORIES:
        if category not in merged:
            merged[category] = "docker"
            changed = True
    # Forcing docker for untrusted categories newly references the docker
    # backend, so layer the hardened gVisor runtime defaults on too;
    # otherwise code_execution/terminal would run on plain docker. Idempotent
    # and a no-op when docker is not referenced.
    secure_config = (
        config if not changed else config.model_copy(update={"overrides": merged})
    )
    return merge_gvisor_defaults(secure_config)


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

    Interpreter-critical exceptions (``MemoryError`` / ``RecursionError``)
    are the exception: they are re-raised rather than logged, so a fatal
    condition propagates instead of being demoted to a warning.

    Args:
        backends: Mapping of backend name to backend instance.

    Raises:
        MemoryError: Propagated when a backend cleanup raises it.
        RecursionError: Propagated when a backend cleanup raises it.
    """

    async def _cleanup_one(name: str, backend: SandboxBackend) -> None:
        """Clean up one backend; broad-except so one failure cannot cancel siblings."""
        logger.debug(SANDBOX_FACTORY_CLEANUP, backend=name)
        try:
            await backend.cleanup()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
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
    # that escaped _cleanup_one's except Exception block. gather captured
    # them as results, so reraise_critical must run here too: otherwise an
    # interpreter-critical error (MemoryError / RecursionError) re-raised
    # inside _cleanup_one would be silently demoted to a log line.
    for (name, _), result in zip(backend_items, results, strict=True):
        if isinstance(result, BaseException):
            reraise_critical(result)
            log_exception_redacted(
                logger,
                SANDBOX_FACTORY_CLEANUP_FAILED,
                result,
                backend=name,
                reason="unhandled_exception_during_cleanup",
            )


async def cleanup_tracked_sandbox_backends() -> None:
    """Clean up every backend this process built, whoever built it.

    The entry point a shutdown calls. Its population comes from
    ``_BUILT_BACKENDS`` rather than from a mapping the caller holds, because no
    caller holds them all: see that constant for which four sites build them
    and why a listed population is the wrong shape here.

    What this covers is the window the other two mechanisms leave open.
    Containers are already released per task by the execution service, and
    reclaimed at boot by the sandbox-reconciliation subsystem for whatever a
    previous incarnation left behind. Containers alive when this process stops
    fall between the two: they survive until a next boot that may never come,
    because an operator scaling down is not an operator restarting.

    Keys are suffixed with an index because the name alone does not identify a
    backend here: three of the four sites build one called ``docker``, and a
    log line naming the third of them ``docker`` says nothing about which.

    Raises:
        MemoryError: Propagated from a backend's cleanup.
        RecursionError: Propagated from a backend's cleanup.
    """
    tracked = [
        (f"{name}#{index}", backend)
        for index, (backend, name) in enumerate(_BUILT_BACKENDS.items())
    ]
    if not tracked:
        return
    await cleanup_sandbox_backends(backends=dict(tracked))
