# module-kind: adapter
"""Background-shell-command boot wiring for engine assembly.

Split out of ``_engine_assembly.py`` (which sits at its module-size
cap) rather than added there: the repository lookup, the two Shape-B
settings reads, and the ``pin_check`` construction-order bind are one
cohesive unit of "does this deployment have background shell commands
wired, and if so, connect the pieces" that ``_build_tool_registry``
otherwise has to inline.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.sandbox import SANDBOX_LIFECYCLE_PIN_CHECK_UNBOUND
from synthorg.persistence.background_job_protocol import BackgroundJobRepository
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.sandbox.lifecycle.per_agent import PerAgentStrategy
from synthorg.tools.sandbox.lifecycle.per_task import PerTaskStrategy
from synthorg.tools.sandbox.lifecycle.protocol import SandboxLifecycleStrategy
from synthorg.tools.sandbox.protocol import SandboxBackend

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_TOOLS_NS: str = "tools"
_MAX_CONCURRENT_JOBS_KEY: str = "shell_command_background_max_concurrent_jobs"
_OUTPUT_BYTE_CAP_KEY: str = "shell_command_background_output_byte_cap"


def background_job_repo_or_none(
    app_state: AppState,
) -> BackgroundJobRepository | None:
    """Resolve the background-job store, or ``None`` before persistence connects.

    A Docker backend built without this repository cannot start, poll,
    read or cancel a background job at all (``start_background`` refuses
    with ``SandboxBackgroundUnsupportedError``): the feature is entirely
    persistence-backed, unlike the container tracker which merely
    degrades to an in-memory dict.

    Returns:
        The repository, or ``None``.
    """
    persistence = app_state.slice(PersistenceStateSlice).backend
    if persistence is None or not persistence.is_connected:
        return None
    return persistence.background_jobs


def background_job_registry_or_none(
    app_state: AppState,
) -> BackgroundJobRegistry | None:
    """Wrap :func:`background_job_repo_or_none` in a registry, if wired.

    Every caller needing read/write access to background-job rows
    builds its own :class:`BackgroundJobRegistry` from this same
    repository and clock rather than sharing one instance: the class
    holds no state of its own, so a second instance over the same
    repository is a distinction without a difference.

    Returns:
        A registry over the resolved repository, or ``None``.
    """
    repo = background_job_repo_or_none(app_state)
    if repo is None:
        return None
    return BackgroundJobRegistry(repo, clock=app_state.clock)


async def resolve_background_job_ceilings(
    resolver: ConfigResolverProtocol,
) -> tuple[int, int]:
    """Resolve the two Shape-B background-job ceilings.

    Returns:
        ``(max_concurrent_jobs, output_byte_cap)``.
    """
    max_concurrent_jobs = await resolver.get_int(_TOOLS_NS, _MAX_CONCURRENT_JOBS_KEY)
    output_byte_cap = await resolver.get_int(_TOOLS_NS, _OUTPUT_BYTE_CAP_KEY)
    return max_concurrent_jobs, output_byte_cap


def bind_pin_check_if_wired(
    *,
    lifecycle_strategy: SandboxLifecycleStrategy,
    sandbox_backends: Mapping[str, SandboxBackend],
    background_jobs: BackgroundJobRegistry | None,
) -> None:
    """Bind ``pin_check`` once both the strategy and the Docker backend exist.

    Breaks the construction-order cycle documented on
    ``PerAgentStrategy.bind_pin_check`` / ``PerTaskStrategy.bind_pin_check``:
    the strategy has to exist before ``build_sandbox_backends`` can
    construct the sandbox, and ``pin_check`` is a bound method of that
    sandbox. Safe because grace/idle expiry only ever reads ``pin_check``
    for a container already acquired, and none has been yet.
    """
    if background_jobs is None:
        logger.debug(
            SANDBOX_LIFECYCLE_PIN_CHECK_UNBOUND, reason="background_jobs_unwired"
        )
        return
    if not isinstance(lifecycle_strategy, PerAgentStrategy | PerTaskStrategy):
        logger.debug(
            SANDBOX_LIFECYCLE_PIN_CHECK_UNBOUND,
            reason="strategy_not_reusable",
            strategy=type(lifecycle_strategy).__name__,
        )
        return
    docker_backend = sandbox_backends.get("docker")
    if isinstance(docker_backend, DockerSandbox):
        lifecycle_strategy.bind_pin_check(docker_backend.pin_check)
    else:
        logger.debug(SANDBOX_LIFECYCLE_PIN_CHECK_UNBOUND, reason="no_docker_backend")


__all__ = [
    "background_job_registry_or_none",
    "background_job_repo_or_none",
    "bind_pin_check_if_wired",
    "resolve_background_job_ceilings",
]
