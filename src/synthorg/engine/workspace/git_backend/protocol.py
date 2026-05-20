"""Pluggable git-backend protocol and result models.

A :class:`GitBackend` owns "where git lives" for a project: it
provisions the repository, and serialised pushes/fetches flow through
it.  Implementations are interchangeable behind the
:class:`~synthorg.core.enums.GitBackendType` discriminator so switching
storage is a config change only.
"""

from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649 introspection)
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.core.enums import GitBackendType  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class ProvisionResult(BaseModel):
    """Outcome of provisioning a project's git repository."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    repo_root: NotBlankStr
    default_branch: NotBlankStr
    newly_created: bool


class PushResult(BaseModel):
    """Outcome of a push to the backend."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    branch: NotBlankStr
    head_sha: NotBlankStr


class FetchResult(BaseModel):
    """Outcome of a fetch from the backend."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    updated_refs: tuple[NotBlankStr, ...] = ()


@runtime_checkable
class GitBackend(Protocol):
    """Pluggable git storage strategy for a project workspace.

    Failures raise a typed
    :class:`~synthorg.engine.errors.GitBackendError` subclass rather
    than returning an error string, so secret-shaped exception text
    can never leak into result fields.
    """

    async def provision(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,
        default_branch: NotBlankStr,
    ) -> ProvisionResult:
        """Ensure the project's git repository exists and is initialised.

        Idempotent: a second call on an already-provisioned workspace
        returns ``newly_created=False`` without mutating history.

        Raises:
            GitBackendProvisionError: Repository creation failed.
        """
        ...

    async def push(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
        branch: NotBlankStr,
        base_branch: NotBlankStr,
    ) -> PushResult:
        """Push *branch* to the backend.

        Raises:
            GitBackendPushError: The push was rejected or failed.
        """
        ...

    async def fetch(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
        branch: NotBlankStr | None = None,
    ) -> FetchResult:
        """Fetch updates from the backend into *repo_root*.

        Raises:
            GitBackendFetchError: The fetch failed.
        """
        ...

    def get_backend_type(self) -> GitBackendType:
        """Return the discriminator identifying this backend."""
        ...
