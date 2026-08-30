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

from synthorg.core.types import NotBlankStr
from synthorg.persistence.background_job_protocol import BackgroundJobRecord
from synthorg.tools.sandbox.errors import SandboxBackgroundJobNotFoundError
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


class BackgroundJobCall(NamedTuple):
    """One recorded ``start_background`` call.

    Attributes:
        command: The program invoked.
        args: Its argument vector.
        cwd: Working directory, or ``None`` for the workspace root.
        env_overrides: Environment the caller added.
        category: The tool category (decides the mount-mode segment of
            the resolved owner key on a real backend).
        owner_id: Explicit lifecycle owner, or ``None`` to derive one
            (this double records exactly what it was given -- it does
            not perform real resolution).
        project_id: The project whose workspace is mounted.
    """

    command: str
    args: tuple[str, ...]
    cwd: Path | None
    env_overrides: dict[str, str]
    category: str
    owner_id: str | None
    project_id: str | None


_DEFAULT_BACKGROUND_JOB_ID = NotBlankStr("job-1")


class FakeSandbox:
    """Returns a canned result (or raises) and records the call it was given.

    Attributes:
        calls: Every ``execute`` call, in order.
        released: Every owner id passed to ``release_owner``.
        cleaned_up: Whether the backend was torn down.
        background_calls: Every ``start_background`` call, in order.
        cancelled_job_ids: Every job id passed to ``cancel_background``.
    """

    def __init__(
        self,
        result: SandboxResult | None = None,
        *,
        error: Exception | None = None,
        backend_type: str = "subprocess",
        background_job_id: NotBlankStr = _DEFAULT_BACKGROUND_JOB_ID,
        background_record: BackgroundJobRecord | None = None,
        background_output: str = "",
        background_jobs: tuple[BackgroundJobRecord, ...] = (),
        background_error: Exception | None = None,
    ) -> None:
        self._result = result or SandboxResult(stdout="", stderr="", returncode=0)
        self._error = error
        self._backend_type = backend_type
        self.calls: list[SandboxCall] = []
        self.released: list[str] = []
        self.cleaned_up = False
        self._background_job_id = background_job_id
        self._background_record = background_record
        self._background_output = background_output
        self._background_jobs = background_jobs
        self._background_error = background_error
        self.background_calls: list[BackgroundJobCall] = []
        self.cancelled_job_ids: list[str] = []

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

    async def start_background(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        category: str = "",
        owner_id: NotBlankStr | None = None,
        project_id: NotBlankStr | None = None,
        max_duration_seconds: float | None = None,
    ) -> NotBlankStr:
        """Record the call and return the canned job id.

        Returns:
            The job id this double was built with.

        Raises:
            Exception: The ``background_error`` this double was built
                with, if any.
        """
        self.background_calls.append(
            BackgroundJobCall(
                command=command,
                args=args,
                cwd=cwd,
                env_overrides=dict(env_overrides or {}),
                category=category,
                owner_id=owner_id,
                project_id=project_id,
            )
        )
        if self._background_error is not None:
            raise self._background_error
        return self._background_job_id

    async def poll_background(
        self,
        job_id: NotBlankStr,
        *,
        category: str = "",
        owner_id: NotBlankStr | None = None,
        project_id: NotBlankStr | None = None,
    ) -> BackgroundJobRecord:
        """Return the canned tracking row.

        Returns:
            The record this double was built with.

        Raises:
            SandboxBackgroundJobNotFoundError: No record was configured.
        """
        del category, owner_id, project_id
        if self._background_record is None:
            msg = f"No background job matches {job_id!r}"
            raise SandboxBackgroundJobNotFoundError(msg)
        return self._background_record

    async def read_background_output(
        self,
        job_id: NotBlankStr,
        *,
        byte_cap: int,
        category: str = "",
        owner_id: NotBlankStr | None = None,
        project_id: NotBlankStr | None = None,
    ) -> str:
        """Return the canned output, truncated to *byte_cap* bytes.

        Returns:
            The captured output this double was built with.
        """
        del job_id, category, owner_id, project_id
        return self._background_output[:byte_cap]

    async def cancel_background(
        self,
        job_id: NotBlankStr,
        *,
        category: str = "",
        owner_id: NotBlankStr | None = None,
        project_id: NotBlankStr | None = None,
    ) -> BackgroundJobRecord:
        """Record the call and return the canned tracking row.

        Returns:
            The record this double was built with.

        Raises:
            SandboxBackgroundJobNotFoundError: No record was configured.
        """
        del category, owner_id, project_id
        self.cancelled_job_ids.append(job_id)
        if self._background_record is None:
            msg = f"No background job matches {job_id!r}"
            raise SandboxBackgroundJobNotFoundError(msg)
        return self._background_record

    async def list_background_jobs(
        self,
        owner_id: NotBlankStr | None = None,
        *,
        category: str = "",
        project_id: NotBlankStr | None = None,
    ) -> tuple[BackgroundJobRecord, ...]:
        """Return the canned job list.

        Returns:
            The job rows this double was built with.
        """
        del owner_id, category, project_id
        return self._background_jobs


__all__ = ["BackgroundJobCall", "FakeSandbox", "SandboxCall"]
