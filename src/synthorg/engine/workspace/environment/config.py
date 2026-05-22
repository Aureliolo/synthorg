"""Pluggable per-project environment config + deps bundle.

The :class:`~synthorg.core.enums.EnvironmentType` discriminator selects
one of three strategies.  The safe default is ``MANIFEST`` (a
backend-agnostic bootstrap manifest committed in the workspace, runnable
in both sandboxes and on a fresh clone).  ``DEVCONTAINER`` and ``NIX``
ship so switching the declaration format is a config change only.
Runtime collaborators that cannot live in frozen config (the Docker
client factory for the devcontainer image build, the clock) travel in
:class:`EnvironmentDeps`, mirroring ``GitBackendDeps``.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import EnvironmentType

if TYPE_CHECKING:
    from synthorg.core.clock import Clock
    from synthorg.engine.workspace.environment.image_builder import ImageBuilder

_DEFAULT_MANIFEST_FILENAME: Final[str] = "synthorg.env.yaml"
_DEFAULT_PROVISION_TIMEOUT_SECONDS: Final[float] = 900.0
_DEFAULT_DOCKER_BUILD_TIMEOUT_SECONDS: Final[float] = 1800.0
_DEFAULT_DOCKER_BUILD_MAX_ATTEMPTS: Final[int] = 3
_DEFAULT_DOCKER_BUILD_RETRY_BASE_SECONDS: Final[float] = 2.0
_DEFAULT_DOCKER_BUILD_RETRY_CAP_SECONDS: Final[float] = 30.0


class EnvironmentConfig(BaseModel):
    """Operator-tunable per-project environment configuration.

    Default-constructed (``kind=MANIFEST``, ``auto_seed=True``) declares
    a bootstrap manifest, scaffolding a default one into a fresh
    workspace, and provisions it through whichever sandbox backend the
    build/test tool categories resolve to.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    kind: EnvironmentType = EnvironmentType.MANIFEST
    # Scaffold a default declaration into a fresh workspace when absent.
    auto_seed: bool = True
    # MANIFEST: the committed declaration filename.
    manifest_filename: str = Field(
        default=_DEFAULT_MANIFEST_FILENAME,
        min_length=1,
    )
    # Maximum seconds a single setup command (bootstrap path) may run.
    provision_timeout_seconds: float = Field(
        default=_DEFAULT_PROVISION_TIMEOUT_SECONDS,
        gt=0.0,
    )
    # DEVCONTAINER: maximum seconds the image build may run.
    docker_build_timeout_seconds: float = Field(
        default=_DEFAULT_DOCKER_BUILD_TIMEOUT_SECONDS,
        gt=0.0,
    )
    # DEVCONTAINER: total image-build attempts (incl. the first) for
    # transient failures (registry/network/daemon); a deterministic
    # build failure (bad Dockerfile) is never retried.
    docker_build_max_attempts: int = Field(
        default=_DEFAULT_DOCKER_BUILD_MAX_ATTEMPTS,
        ge=1,
    )
    docker_build_retry_base_seconds: float = Field(
        default=_DEFAULT_DOCKER_BUILD_RETRY_BASE_SECONDS,
        ge=0.0,
    )
    docker_build_retry_cap_seconds: float = Field(
        default=_DEFAULT_DOCKER_BUILD_RETRY_CAP_SECONDS,
        ge=0.0,
    )


@dataclass(frozen=True, slots=True)
class EnvironmentDeps:
    """Runtime collaborators the frozen config cannot carry.

    Attributes:
        image_builder: Builds the sealed image for ``DEVCONTAINER`` (the
            ``build`` / Dockerfile path).  When ``None`` the factory
            wires the default ``SubprocessImageBuilder`` (spawns
            ``docker build`` on the host daemon).  Tests inject a fake.
        clock: Clock seam for provisioning timestamps.
    """

    image_builder: ImageBuilder | None = None
    clock: Clock | None = None
