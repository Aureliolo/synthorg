"""Pluggable git-backend protocol and result models.

A :class:`GitBackend` owns "where git lives" for a project: it
provisions the repository, and serialised pushes/fetches flow through
it.  Implementations are interchangeable behind the
:class:`~synthorg.core.project_enums.GitBackendType` discriminator so switching
storage is a config change only.
"""

from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.project_enums import GitBackendType
from synthorg.core.types import NotBlankStr


class SourceKind(StrEnum):
    """Classification of a brownfield import source reference.

    ``REMOTE`` is a clone URL (https/ssh) fetched over the network;
    ``LOCAL_PATH`` is an on-disk repository fetched from the filesystem.
    """

    REMOTE = "remote"
    LOCAL_PATH = "local_path"


class ResolvedSource(BaseModel):
    """A brownfield import source resolved and ready to fetch.

    Built by the service layer (which owns the connection catalog and
    SSRF validation) so the git backend stays auth-agnostic: by the time
    a backend sees it, ``fetch_url`` is fetch-ready (token injected when
    the source matched a forge connection) and ``pre_fetch_config_args``
    carries any ``git -c`` options needed for transport (e.g. DNS-pinning
    ``http.curloptResolve`` for HTTPS sources).

    ``fetch_url`` may embed a credential; never log it. The backend adds
    it as a temporary remote, fetches once, and removes the remote, so it
    never persists in the workspace's git config.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    fetch_url: NotBlankStr = Field(
        description="Fetch-ready source reference (URL or local path)",
    )
    source_kind: SourceKind = Field(description="Source classification")
    pre_fetch_config_args: tuple[str, ...] = Field(
        default=(),
        description="git -c options prepended to the fetch (transport pinning)",
    )


class ProvisionResult(BaseModel):
    """Outcome of provisioning a project's git repository."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    repo_root: NotBlankStr
    default_branch: NotBlankStr
    newly_created: bool


class SeedResult(BaseModel):
    """Outcome of seeding a workspace from an existing source."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    repo_root: NotBlankStr
    default_branch: NotBlankStr
    head_sha: NotBlankStr
    source_kind: SourceKind


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

    async def seed(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
        source: ResolvedSource,
        default_branch: NotBlankStr,
    ) -> SeedResult:
        """Import an existing source into a freshly provisioned workspace.

        One-shot history import: fetches *source* into *repo_root*, resets
        the default branch onto the imported head, and pushes to the
        backend's own origin (the imported codebase becomes the initial
        real content). Distinct from :meth:`provision`, which creates an
        empty repository.

        Requires a provisioned-but-empty workspace (no tracked files).

        Raises:
            GitBackendSeedError: The workspace already holds a codebase,
                or the fetch/reset failed.
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
