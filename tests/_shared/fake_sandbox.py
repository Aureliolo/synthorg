"""One hand-written stand-in for a sandbox backend.

Three suites had grown their own, and the drift between them is the point:
one gave ``args`` a default the protocol does not, so a caller that dropped
it passed there and raised against both real backends. A double is only worth
having while it refuses what the real thing refuses, and keeping one copy is
how that stays true.

``typeguard`` checks the whole protocol against a fake, so every method is
declared with the protocol's exact signature rather than ``**kwargs``.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from synthorg.tools.sandbox.result import SandboxResult


class SandboxCall(NamedTuple):
    """One recorded ``execute`` call.

    A typed record rather than a dict, so a test asserting on ``category``
    gets a name the checker knows and a misspelling fails before the run.

    Attributes:
        command: The program invoked.
        args: Its argument vector.
        cwd: Working directory, or ``None`` for the workspace root.
        env_overrides: Environment the caller added.
        timeout: Per-call ceiling, or ``None`` for the backend default.
        category: The tool category, which chooses BOTH the container runtime
            and whether the workspace mount is writable. Recorded because it
            defaults to the empty string and every shipped call site once
            omitted it.
        owner_id: Lifecycle owner for container reuse.
        project_id: The project whose workspace is mounted.
    """

    command: str
    args: tuple[str, ...]
    cwd: Path | None
    env_overrides: dict[str, str]
    timeout: float | None
    category: str
    owner_id: str | None
    project_id: str | None


class FakeSandbox:
    """Returns a canned result (or raises) and records the call it was given.

    Attributes:
        calls: Every ``execute`` call, in order.
        released: Every owner id passed to ``release_owner``.
        cleaned_up: Whether the backend was torn down.
    """

    def __init__(
        self,
        result: SandboxResult | None = None,
        *,
        error: Exception | None = None,
        backend_type: str = "subprocess",
    ) -> None:
        self._result = result or SandboxResult(stdout="", stderr="", returncode=0)
        self._error = error
        self._backend_type = backend_type
        self.calls: list[SandboxCall] = []
        self.released: list[str] = []
        self.cleaned_up = False

    @property
    def last_call(self) -> SandboxCall | None:
        """The most recent ``execute`` call, or ``None`` if never called."""
        return self.calls[-1] if self.calls else None

    async def execute(
        self,
        *,
        command: str,
        # No default, matching the protocol and both real backends. A default
        # here would accept a call that drops ``args`` while every real
        # backend raises, which is how a hand-written double stops testing the
        # thing it stands in for.
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        category: str = "",
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> SandboxResult:
        """Record the call and return the canned result.

        Returns:
            The result this double was built with.

        Raises:
            Exception: The ``error`` this double was built with, if any.
        """
        self.calls.append(
            SandboxCall(
                command=command,
                args=args,
                cwd=cwd,
                env_overrides=dict(env_overrides or {}),
                timeout=timeout,
                category=category,
                owner_id=owner_id,
                project_id=project_id,
            )
        )
        if self._error is not None:
            raise self._error
        return self._result

    async def release_owner(
        self,
        owner_id: str,
        *,
        project_id: str | None = None,
        image_override: str | None = None,
    ) -> None:
        """Record that *owner_id* was released."""
        del project_id, image_override
        self.released.append(owner_id)

    async def cleanup(self) -> None:
        """Record that the backend was torn down."""
        self.cleaned_up = True

    async def health_check(self) -> bool:
        """Report the backend as reachable.

        Returns:
            Always ``True``.
        """
        return True

    def get_backend_type(self) -> str:
        """Return the backend discriminator this double claims.

        Returns:
            The configured backend type.
        """
        return self._backend_type


__all__ = ["FakeSandbox", "SandboxCall"]
