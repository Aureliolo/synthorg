"""Pluggable git-backend plugin config + deps bundle.

The :class:`~synthorg.core.enums.GitBackendType` discriminator selects
one of three strategies.  The safe default is ``EMBEDDED`` (a bare repo
self-hosted on the persistent volume, no external dependency).
``LOCAL_PATH`` and ``EXTERNAL_REMOTE`` ship so that switching the git
backend is a config change only.  Runtime collaborators that cannot
live in frozen config (the connection catalog, secret backend, clock)
travel in :class:`GitBackendDeps`, mirroring ``AutonomyStrategyDeps``.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import GitBackendType

if TYPE_CHECKING:
    from pathlib import Path

    from synthorg.core.clock import Clock
    from synthorg.integrations.connections.catalog import ConnectionCatalog
    from synthorg.persistence.secret_backends.protocol import SecretBackend

_DEFAULT_EMBEDDED_SUBDIR: Final[str] = "git-repos"
_DEFAULT_GIT_CMD_TIMEOUT_SECONDS: Final[float] = 60.0
_DEFAULT_RETRY_MAX_ATTEMPTS: Final[int] = 3
_RETRY_MAX_ATTEMPTS_CEILING: Final[int] = 10
_DEFAULT_RETRY_BASE_DELAY_SECONDS: Final[float] = 1.0
_DEFAULT_RETRY_CAP_DELAY_SECONDS: Final[float] = 30.0
_DEFAULT_FORGE_API_TIMEOUT_SECONDS: Final[float] = 30.0


class GitBackendResilienceConfig(BaseModel):
    """Backoff tuning for transient external-remote git operations.

    Mirrors the transient-I/O retry semantics of
    :class:`~synthorg.core.resilience.GeneralRetryHandler` (Pattern A):
    push/fetch failures classified as transient (or forge rate-limit)
    retry with exponential backoff; auth / repo-missing failures do not.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_attempts: int = Field(
        default=_DEFAULT_RETRY_MAX_ATTEMPTS, ge=1, le=_RETRY_MAX_ATTEMPTS_CEILING
    )
    base_delay_seconds: float = Field(
        default=_DEFAULT_RETRY_BASE_DELAY_SECONDS,
        ge=0.0,
    )
    cap_delay_seconds: float = Field(
        default=_DEFAULT_RETRY_CAP_DELAY_SECONDS,
        gt=0.0,
    )
    jitter: bool = True

    @model_validator(mode="after")
    def _check_cap_ge_base(self) -> Self:
        """The delay cap cannot be below the base delay."""
        if self.cap_delay_seconds < self.base_delay_seconds:
            msg = "cap_delay_seconds must be >= base_delay_seconds"
            raise ValueError(msg)
        return self


class GitBackendConfig(BaseModel):
    """Operator-tunable git-backend configuration.

    Default-constructed (``kind=EMBEDDED``) provisions a bare repo on
    the persistent volume with no external dependency.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    kind: GitBackendType = GitBackendType.EMBEDDED
    # EMBEDDED: subdirectory under the workspace base holding bare repos.
    embedded_subdir: str = Field(
        default=_DEFAULT_EMBEDDED_SUBDIR,
        min_length=1,
    )
    # LOCAL_PATH: caller-supplied existing git repository path.
    local_repo_path: str | None = Field(default=None, min_length=1)
    # EXTERNAL_REMOTE: connection-catalog name for the forge connection.
    remote_connection_name: str | None = Field(default=None, min_length=1)
    # Maximum seconds any single git subprocess may run.
    git_cmd_timeout_seconds: float = Field(
        default=_DEFAULT_GIT_CMD_TIMEOUT_SECONDS,
        gt=0.0,
    )
    # EXTERNAL_REMOTE: transient-failure backoff tuning for push/fetch.
    resilience: GitBackendResilienceConfig = Field(
        default_factory=GitBackendResilienceConfig,
    )
    # EXTERNAL_REMOTE: create the forge repo on first push if it is
    # missing (lazy on-404 provisioning via the forge REST API).
    forge_provisioning_enabled: bool = True
    # EXTERNAL_REMOTE: visibility of an auto-provisioned forge repo.
    forge_repo_private: bool = True
    # EXTERNAL_REMOTE: per-request timeout for forge REST API calls.
    forge_api_timeout_seconds: float = Field(
        default=_DEFAULT_FORGE_API_TIMEOUT_SECONDS,
        gt=0.0,
    )

    @model_validator(mode="after")
    def _check_kind_requirements(self) -> Self:
        """Each non-default kind needs its addressing field set."""
        if self.kind is GitBackendType.LOCAL_PATH and not self.local_repo_path:
            msg = "LOCAL_PATH git backend requires 'local_repo_path'"
            raise ValueError(msg)
        if (
            self.kind is GitBackendType.EXTERNAL_REMOTE
            and not self.remote_connection_name
        ):
            msg = "EXTERNAL_REMOTE git backend requires 'remote_connection_name'"
            raise ValueError(msg)
        return self


@dataclass(frozen=True, slots=True)
class GitBackendDeps:
    """Runtime collaborators the frozen config cannot carry.

    Attributes:
        workspace_base_root: REQUIRED for ``EMBEDDED`` (the persistent
            volume base under which bare repos are self-hosted).
        connection_catalog: REQUIRED for ``EXTERNAL_REMOTE`` (resolves
            the forge connection by name).
        secret_backend: REQUIRED for ``EXTERNAL_REMOTE`` (resolves the
            connection's access token).
        clock: Clock seam for provisioning timestamps.
    """

    workspace_base_root: Path | None = None
    connection_catalog: ConnectionCatalog | None = None
    secret_backend: SecretBackend | None = None
    clock: Clock | None = None
