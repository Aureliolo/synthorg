# module-kind: code
"""How a sandbox container is given the workspace its parent already holds.

A bind spec travels to the daemon as a string and is resolved in the DAEMON's
namespace. That is the same namespace as the caller's only while the caller runs
on the host. A backend running inside a container that passes its own
``/data/agent-workspaces`` names a host path that generally does not exist, and
Docker creates an empty directory and mounts that: the sandbox starts, sees an
empty ``/workspace``, and every command it runs fails for a reason that has
nothing to do with the command.

So a containerised parent does not pass a path. It asks the daemon how its own
storage is provided and reproduces that: a named volume becomes the same volume
plus the subpath the workspace sits at, and a bind becomes the host side of that
bind plus the same relative remainder. Reproducing the subpath rather than
mounting the volume whole is what preserves per-project isolation, which is a
security property rather than a convenience.

An uncontainerised parent resolves to ``None`` here and keeps the caller's
existing host-path bind untouched, so nothing about the host-run path changes.

The one thing this module will not do is guess. A containerised parent whose
workspace root is covered by none of its own mounts raises, because the
alternative is the silent empty mount above.
"""

import re
import socket
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Final

import aiodocker

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_WORKSPACE_MOUNT_RESOLVED,
    SANDBOX_WORKSPACE_MOUNT_UNRESOLVED,
)
from synthorg.tools.sandbox.errors import (
    SandboxSubpathUnsupportedError,
    SandboxWorkspaceUnmappableError,
)

logger = get_logger(__name__)

#: Where the kernel describes this process's own mounts. Every Docker container
#: carries its own id in the source of the files the daemon injects
#: (``/etc/resolv.conf``, ``/etc/hostname``), which makes this the one
#: self-identification that does not depend on how the container was named.
MOUNTINFO_PATH: Final[Path] = Path("/proc/self/mountinfo")

_MOUNTINFO_CONTAINER_RE: Final[re.Pattern[str]] = re.compile(
    r"/containers/([0-9a-f]{64})/"
)

#: A container's default hostname is its own short id. Used only as a fallback,
#: and treated as a guess: an ordinary machine could in principle be named this,
#: so an inspect that finds no such container means "not containerised" rather
#: than an error.
_SHORT_ID_RE: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]{12,64}\Z")

#: ``Mounts[].VolumeOptions.Subpath`` landed in Docker Engine 26.0 / API 1.45.
#: Below it there is no way to mount part of a volume, and mounting the whole
#: volume instead would hand a project-A sandbox project-B's files.
MIN_SUBPATH_API_VERSION: Final[tuple[int, int]] = (1, 45)

_API_VERSION_PARTS: Final[int] = 2

_MOUNT_TYPE_VOLUME: Final[str] = "volume"
_MOUNT_TYPE_BIND: Final[str] = "bind"


@dataclass(frozen=True, slots=True)
class WorkspaceMount:
    """The storage a sandbox container is given, as the daemon sees it.

    Exactly one of *volume* and *host_path* is set: a mount is either a named
    volume the daemon owns or a path on the host, and a value carrying both
    would leave the caller to pick, which is the ambiguity this type exists to
    remove.

    Attributes:
        volume: Named Docker volume, when the parent holds one.
        subpath: POSIX path within *volume*, empty at its mount point. Only
            meaningful alongside *volume*.
        host_path: Path in the daemon's own namespace, when the parent's
            storage is a bind rather than a volume.
    """

    volume: str | None = None
    subpath: str = ""
    host_path: str | None = None

    def __post_init__(self) -> None:
        """Refuse a mount that names both kinds of storage, or neither.

        Raises:
            ValueError: When *volume* and *host_path* are both set or both
                absent, or when *subpath* is supplied without a volume.
        """
        if (self.volume is None) == (self.host_path is None):
            msg = (
                "WorkspaceMount must name exactly one of volume or host_path, "
                f"got volume={self.volume!r} host_path={self.host_path!r}"
            )
            raise ValueError(msg)
        if self.host_path is not None and self.subpath:
            msg = f"WorkspaceMount host_path carries no subpath, got {self.subpath!r}"
            raise ValueError(msg)

    def child(self, relative: PurePosixPath) -> WorkspaceMount:
        """Return the mount for *relative* below this one.

        The per-execution mount root is a project subtree of the workspace, so
        the resolution above happens once for the root and each execution asks
        for its own subtree.

        Args:
            relative: Path below this mount, relative and POSIX.

        Returns:
            The same storage, addressed one level in.
        """
        suffix = "" if str(relative) == "." else str(relative)
        if not suffix:
            return self
        if self.volume is not None:
            joined = f"{self.subpath}/{suffix}" if self.subpath else suffix
            return replace(self, subpath=joined)
        return replace(self, host_path=f"{self.host_path}/{suffix}")


def container_id_from_mountinfo(path: Path = MOUNTINFO_PATH) -> str | None:
    """Return this process's container id, read from its own mount table.

    Args:
        path: Where to read the mount table from.

    Returns:
        The full container id, or ``None`` when this process is not running
        in a Docker container (including every non-Linux host, where the file
        does not exist).
    """
    try:
        table = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _MOUNTINFO_CONTAINER_RE.search(table)
    return match.group(1) if match is not None else None


def container_id_from_hostname(hostname: str) -> str | None:
    """Return *hostname* when it looks like a container id.

    Args:
        hostname: The machine name this process reports.

    Returns:
        The id, or ``None`` when the name is an ordinary hostname.
    """
    return hostname if _SHORT_ID_RE.fullmatch(hostname) else None


@dataclass(frozen=True, slots=True)
class OwnContainer:
    """Which container this process is, and how sure it is.

    Attributes:
        container_id: The container, or ``None`` when running on the host.
        certain: Whether the id was read from this process's own mount table
            rather than guessed from its hostname. A guess that names no
            container means "not containerised"; a known id the daemon will
            not describe is a failure.
    """

    container_id: str | None
    certain: bool = True


def discover_own_container() -> OwnContainer:
    """Work out which container, if any, this process is running in.

    The one place that answers it. Both the sandbox backend (resolving a
    mount) and the boot probe (reporting whether it can) ask here, because two
    implementations of "which container am I" is one more than can be kept in
    step.

    Returns:
        The identity, with ``container_id=None`` on the host.
    """
    from_mountinfo = container_id_from_mountinfo()
    if from_mountinfo is not None:
        return OwnContainer(container_id=from_mountinfo)
    return OwnContainer(
        container_id=container_id_from_hostname(socket.gethostname()),
        certain=False,
    )


def _supports_subpath(api_version: str) -> bool:
    """Whether a daemon speaking *api_version* can mount part of a volume.

    An unparseable version reads as unsupported: the mount below is a
    correctness boundary, and guessing "probably new enough" is how a
    per-project isolation guarantee turns into a silent whole-volume mount.

    Returns:
        Whether ``VolumeOptions.Subpath`` is available.
    """
    parts = api_version.split(".")
    if len(parts) != _API_VERSION_PARTS:
        return False
    try:
        parsed = (int(parts[0]), int(parts[1]))
    except ValueError:
        return False
    return parsed >= MIN_SUBPATH_API_VERSION


def _relative_to(destination: str, root: PurePosixPath) -> PurePosixPath | None:
    """Return *root* relative to *destination*, or ``None`` when outside it.

    Returns:
        The relative remainder, or ``None``.
    """
    try:
        return root.relative_to(PurePosixPath(destination))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One of the parent's mounts that covers the workspace root."""

    entry: dict[str, object]
    relative: PurePosixPath
    depth: int


def _covering_mount(
    mounts: list[dict[str, object]], root: PurePosixPath
) -> _Candidate | None:
    """Return the parent mount that most closely covers *root*.

    The deepest destination wins: a container holding both ``/data`` and
    ``/data/agent-workspaces`` reaches the workspace through the second, and
    resolving through the first would name a subpath of the wrong volume.

    Returns:
        The best candidate, or ``None`` when no mount covers *root*.
    """
    best: _Candidate | None = None
    for entry in mounts:
        destination = entry.get("Destination")
        if not isinstance(destination, str) or not destination:
            continue
        relative = _relative_to(destination, root)
        if relative is None:
            continue
        depth = len(PurePosixPath(destination).parts)
        if best is None or depth > best.depth:
            best = _Candidate(entry=entry, relative=relative, depth=depth)
    return best


async def _inspect_mounts(
    docker: aiodocker.Docker, container_id: str
) -> list[dict[str, object]] | None:
    """Return the mounts *container_id* holds, or ``None`` when unreadable.

    Returns:
        The ``Mounts`` array, or ``None`` when the daemon has no such
        container or would not answer.
    """
    try:
        detail = await docker.containers.container(container_id).show()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SANDBOX_WORKSPACE_MOUNT_UNRESOLVED,
            container_id=container_id[:12],
            reason="inspect_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    mounts = detail.get("Mounts")
    return [entry for entry in mounts if isinstance(entry, dict)] if mounts else []


def _mount_from(candidate: _Candidate, *, api_version: str) -> WorkspaceMount:
    """Build the sandbox mount reproducing *candidate*.

    Returns:
        The volume-plus-subpath or translated-host-path mount.

    Raises:
        SandboxSubpathUnsupportedError: The mount needs a volume subpath the
            daemon cannot serve.
        SandboxWorkspaceUnmappableError: The mount is of a kind that cannot be
            handed to another container (a tmpfs, say), or is missing the field
            that identifies it.
    """
    entry = candidate.entry
    suffix = "" if str(candidate.relative) == "." else str(candidate.relative)
    kind = entry.get("Type")
    if kind == _MOUNT_TYPE_VOLUME:
        name = entry.get("Name")
        if not isinstance(name, str) or not name:
            msg = f"parent volume mount at {entry.get('Destination')!r} has no name"
            raise SandboxWorkspaceUnmappableError(msg)
        if suffix and not _supports_subpath(api_version):
            major, minor = MIN_SUBPATH_API_VERSION
            msg = (
                f"the workspace sits at {suffix!r} inside volume {name!r}, which "
                f"needs Docker API {major}.{minor} (VolumeOptions.Subpath) to "
                f"mount on its own; this daemon speaks {api_version!r}. Mounting "
                "the whole volume instead would let one project's sandbox read "
                "another's files"
            )
            raise SandboxSubpathUnsupportedError(msg)
        return WorkspaceMount(volume=name, subpath=suffix)
    if kind == _MOUNT_TYPE_BIND:
        source = entry.get("Source")
        if not isinstance(source, str) or not source:
            msg = f"parent bind mount at {entry.get('Destination')!r} has no source"
            raise SandboxWorkspaceUnmappableError(msg)
        return WorkspaceMount(host_path=f"{source}/{suffix}" if suffix else source)
    msg = (
        f"the workspace is reached through a {kind!r} mount, which cannot be "
        "handed to another container"
    )
    raise SandboxWorkspaceUnmappableError(msg)


async def resolve_workspace_mount(
    *,
    docker: aiodocker.Docker,
    root: Path,
    api_version: str,
    container_id: str | None,
    certain: bool = True,
) -> WorkspaceMount | None:
    """Resolve how a sibling sandbox reaches *root*.

    Args:
        docker: Client for the daemon that will create the sandbox.
        root: The workspace root as this process sees it.
        api_version: The daemon's API version, as ``GET /version`` reports it.
        container_id: This process's own container, or ``None`` when it is not
            running in one.
        certain: Whether *container_id* is known rather than guessed. A guess
            that names no container means this process is not containerised; a
            known id that cannot be inspected is a failure.

    Returns:
        The mount to reproduce, or ``None`` when this process runs on the host
        and its own paths are already the daemon's.

    Raises:
        SandboxWorkspaceUnmappableError: This process is containerised and
            *root* is covered by none of its own mounts, so any bind built from
            it would resolve to an empty directory in the daemon's namespace.
    """
    if container_id is None:
        return None
    # Every path below is stated the way the DAEMON reads it, which is the only
    # namespace that decides what a sandbox actually receives.
    posix_root = PurePosixPath(root.as_posix())
    mounts = await _inspect_mounts(docker, container_id)
    if mounts is None:
        if not certain:
            return None
        msg = (
            f"this process reports container {container_id[:12]} but the daemon "
            "would not describe it, so there is no way to tell how the workspace "
            f"at {posix_root} reaches a sibling container"
        )
        raise SandboxWorkspaceUnmappableError(msg)
    candidate = _covering_mount(mounts, posix_root)
    if candidate is None:
        destinations = sorted(
            str(entry.get("Destination"))
            for entry in mounts
            if isinstance(entry.get("Destination"), str)
        )
        logger.warning(
            SANDBOX_WORKSPACE_MOUNT_UNRESOLVED,
            container_id=container_id[:12],
            reason="no_covering_mount",
            workspace=str(posix_root),
            destinations=destinations,
        )
        msg = (
            f"the workspace root {posix_root} is not on any mount this container "
            f"holds (it holds {destinations}), so a sandbox given that path would "
            "receive an empty directory rather than the workspace"
        )
        raise SandboxWorkspaceUnmappableError(msg)
    mount = _mount_from(candidate, api_version=api_version)
    logger.info(
        SANDBOX_WORKSPACE_MOUNT_RESOLVED,
        container_id=container_id[:12],
        workspace=str(posix_root),
        volume=mount.volume,
        subpath=mount.subpath,
        host_path=mount.host_path,
    )
    return mount


__all__ = [
    "MIN_SUBPATH_API_VERSION",
    "MOUNTINFO_PATH",
    "OwnContainer",
    "WorkspaceMount",
    "container_id_from_hostname",
    "container_id_from_mountinfo",
    "discover_own_container",
    "resolve_workspace_mount",
]
