"""Ambient per-task sandbox environment (set by the worker, read here).

Tools hold a long-lived sandbox backend injected at registry-build time,
so the per-project reproducible environment (a built devcontainer image
to run in, and toolchain / PATH additions) cannot be a construction-time
choice.  It flows as ambient context instead: the worker execution path
sets :func:`active_sandbox_environment` for the scope of one agent run,
and the Docker / subprocess backends read it when they build a container
or resolve the exec environment.  ``contextvars`` is copied at task
creation, so the engine's ``TaskGroup`` tool fan-out inherits it.
"""

import contextvars
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class ActiveSandboxEnvironment(BaseModel):
    """The reproducible environment to apply to the current agent run.

    Attributes:
        image_override: Built devcontainer image the Docker backend runs
            tool commands in (under the existing hardened host config);
            ``None`` for the bootstrap (manifest / nix) paths.
        env_additions: Toolchain / PATH additions merged into the exec
            environment for every sandbox tool call this run.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    image_override: NotBlankStr | None = None
    env_additions: Mapping[str, str] = Field(default_factory=dict)


_active: contextvars.ContextVar[ActiveSandboxEnvironment | None] = (
    contextvars.ContextVar("synthorg_active_sandbox_environment", default=None)
)


@contextmanager
def active_sandbox_environment(
    environment: ActiveSandboxEnvironment | None,
) -> Iterator[None]:
    """Bind *environment* as the active sandbox environment for the scope.

    Yields:
        Each ``None`` produced by the iterator.
    """
    token = _active.set(environment)
    try:
        yield
    finally:
        _active.reset(token)


def get_active_sandbox_environment() -> ActiveSandboxEnvironment | None:
    """Return the active sandbox environment, or ``None`` when unset.

    Returns:
        The matching ``ActiveSandboxEnvironment``, or ``None`` when no match is found.
    """
    return _active.get()


__all__ = [
    "ActiveSandboxEnvironment",
    "active_sandbox_environment",
    "get_active_sandbox_environment",
]
