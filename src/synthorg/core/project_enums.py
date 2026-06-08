"""Project-family enumerations."""

from enum import StrEnum


class ProjectStatus(StrEnum):
    """Lifecycle status of a project."""

    PLANNING = "planning"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class GitBackendType(StrEnum):
    """Discriminator selecting how a project's git repository is stored.

    ``EMBEDDED`` is the safe default: the product self-hosts a bare repo
    on the persistent volume, with no external dependency.  ``LOCAL_PATH``
    targets a caller-supplied repository on disk.  ``EXTERNAL_REMOTE``
    delegates to an external forge remote resolved via the connection
    catalogue.
    """

    EMBEDDED = "embedded"
    EXTERNAL_REMOTE = "external_remote"
    LOCAL_PATH = "local_path"


class EnvironmentType(StrEnum):
    """Discriminator selecting how a project declares its dev environment.

    ``MANIFEST`` is the safe default: a backend-agnostic bootstrap manifest
    (committed lockfiles plus ordered setup commands) that provisions into
    the mounted workspace and runs in both the subprocess and Docker
    sandboxes, and emits a stock ``bootstrap.sh`` so a fresh clone is
    reproducible without the product present.  ``DEVCONTAINER`` builds a
    sealed Docker image from ``.devcontainer/devcontainer.json`` (Docker
    backend only).  ``NIX`` provisions a hermetic environment from a
    ``flake.nix`` via ``nix develop``.
    """

    MANIFEST = "manifest"
    DEVCONTAINER = "devcontainer"
    NIX = "nix"
