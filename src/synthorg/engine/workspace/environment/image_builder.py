"""Docker image builder for the devcontainer strategy.

The devcontainer strategy builds a sealed image from a ``build`` /
Dockerfile declaration.  The build runs on the host daemon via the
``docker`` CLI (the backend has ``docker.sock`` mounted), spawned as a
subprocess rather than through ``aiodocker`` so the build context does
not have to be tarred and streamed by hand, and so the failure surface
stays small enough to mock in unit tests.
"""

import asyncio
import contextlib
from pathlib import Path
from typing import Final, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import EnvironmentConfigError
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import ENVIRONMENT_IMAGE_BUILD_FAILED

logger = get_logger(__name__)

_DOCKER: Final[str] = "docker"


class BuildOutcome(BaseModel):
    """Immutable result of a docker image build.

    Attributes:
        tag: The image tag the build targeted.
        exit_code: ``docker build`` exit status (``-1`` on timeout).
        log: Combined build output (stdout + stderr), for diagnostics.
        timed_out: Whether the build was killed at the timeout.
        success: Computed -- ``True`` when ``exit_code`` is 0.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tag: NotBlankStr
    exit_code: int
    log: str = ""
    timed_out: bool = False

    @computed_field
    @property
    def success(self) -> bool:
        """Whether the build exited cleanly."""
        return self.exit_code == 0 and not self.timed_out

    @model_validator(mode="after")
    def _check_timeout_marker(self) -> Self:
        """A timed-out build is signalled by the reserved ``-1`` exit code.

        Returns:
            ``self`` unchanged when ``timed_out`` and ``exit_code``
            agree.

        Raises:
            ValueError: When ``timed_out`` is ``True`` and
                ``exit_code`` is not the reserved ``-1`` marker.
        """
        if self.timed_out and self.exit_code != -1:
            msg = "timed_out build must use exit_code -1"
            raise ValueError(msg)
        return self


@runtime_checkable
class ImageBuilder(Protocol):
    """Builds a Docker image from a Dockerfile and context directory."""

    async def build(
        self,
        *,
        tag: NotBlankStr,
        dockerfile: Path,
        context_dir: Path,
        timeout: float,  # noqa: ASYNC109 -- caller-tuned build ceiling
    ) -> BuildOutcome:
        """Build *dockerfile* in *context_dir*, tagged *tag*."""
        ...


class SubprocessImageBuilder:
    """Builds images by spawning ``docker build`` on the host daemon."""

    @staticmethod
    def _assert_contained(dockerfile: Path, context_dir: Path) -> None:
        """Reject a Dockerfile that escapes the build context.

        ``docker build`` requires the Dockerfile to live inside the
        build context, and a caller-supplied path that resolves (through
        symlinks) outside ``context_dir`` would let an attacker spawn a
        build reading arbitrary host files. Both paths are fully
        resolved before the containment check so a symlink cannot slip
        the Dockerfile out of the context after validation.

        Raises:
            EnvironmentConfigError: When ``dockerfile`` does not resolve
                to a path inside ``context_dir`` (422).
        """
        resolved_context = context_dir.resolve()
        resolved_dockerfile = dockerfile.resolve()
        if not resolved_dockerfile.is_relative_to(resolved_context):
            msg = (
                f"Dockerfile {resolved_dockerfile} is outside the build "
                f"context {resolved_context}"
            )
            raise EnvironmentConfigError(msg)

    @staticmethod
    async def _kill_and_reap(proc: asyncio.subprocess.Process) -> None:
        """Kill *proc* and reap it, shielding the wait from cancellation.

        Without the shield an outer cancellation arriving during
        ``proc.wait()`` would unwind before the process is reaped,
        leaking a zombie ``docker build``.
        """
        # The process may exit between the timeout firing and the kill;
        # suppress the kill-race so the cancellation/timeout path still reaps.
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
            await asyncio.shield(proc.wait())

    async def build(
        self,
        *,
        tag: NotBlankStr,
        dockerfile: Path,
        context_dir: Path,
        timeout: float,  # noqa: ASYNC109 -- caller-tuned build ceiling
    ) -> BuildOutcome:
        """Run ``docker build`` and capture its combined output.

        Returns:
            A :class:`BuildOutcome` carrying the tag, exit code,
            combined log, and ``timed_out`` flag.

        Raises:
            EnvironmentConfigError: When ``dockerfile`` resolves outside
                ``context_dir`` (path-containment guard, before spawn).
            CancelledError: Propagated after the subprocess is
                killed and reaped (the kill-and-reap pair runs under
                ``asyncio.shield`` to avoid leaking a zombie).
        """
        self._assert_contained(dockerfile, context_dir)
        proc = await asyncio.create_subprocess_exec(
            _DOCKER,
            "build",
            "-t",
            str(tag),
            "-f",
            str(dockerfile),
            str(context_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            await self._kill_and_reap(proc)
            logger.warning(
                ENVIRONMENT_IMAGE_BUILD_FAILED,
                tag=str(tag),
                reason="timeout",
                timeout_seconds=timeout,
            )
            return BuildOutcome(tag=tag, exit_code=-1, timed_out=True)
        except asyncio.CancelledError:
            # Reap the build before unwinding so a cancelled provision
            # cannot leave a zombie ``docker build`` holding the daemon.
            await self._kill_and_reap(proc)
            raise
        return BuildOutcome(
            tag=tag,
            exit_code=proc.returncode if proc.returncode is not None else -1,
            log=stdout.decode("utf-8", errors="replace"),
        )


__all__ = ["BuildOutcome", "ImageBuilder", "SubprocessImageBuilder"]
