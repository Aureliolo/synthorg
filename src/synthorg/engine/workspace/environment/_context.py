# module-kind: code
"""Assembling a devcontainer build context for the daemon.

The daemon takes the build context as a tar stream and knows nothing about
``.dockerignore``: the ``docker`` CLI is what parses it and omits the matching
paths before uploading. Building through ``aiodocker`` moves that
responsibility here, along with the CLI's unconditional exclusions and the
containment check on the Dockerfile.

Separate from the builder that drives the daemon, because packing a directory
into a bounded tar shares no state with streaming a build and failing it: the
one thing they exchange is the resolved context this module returns.
"""

import io
import tarfile
from pathlib import Path
from typing import Final, NamedTuple

from synthorg.engine.errors import EnvironmentConfigError
from synthorg.engine.workspace.environment._dockerignore import DockerignoreMatcher

CONTEXT_MAX_BYTES_KEY: Final[str] = "devcontainer_context_max_bytes"

#: Never packed, whatever the declaration or the ignore file says.
#: ``.git`` holds the workspace's remote configuration and its whole
#: object history, and an agent-authored ``COPY . /app`` would otherwise
#: bake both into an image layer that is cached by declaration hash and
#: reused. The ignore file itself is the CLI's own exclusion. Matched on
#: any segment, not only at the context root, so a nested checkout is
#: covered too.
_ALWAYS_EXCLUDED: Final[frozenset[str]] = frozenset({".git", ".dockerignore"})


class ContextTooLargeError(EnvironmentConfigError):
    """The build context exceeds the operator's ceiling.

    Attributes:
        packed_bytes: What had been counted when the ceiling was crossed.
        limit_bytes: The ceiling that was crossed.
    """

    def __init__(self, packed_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f"build context exceeds {limit_bytes} bytes (reached "
            f"{packed_bytes}); exclude what the build does not need via "
            f".dockerignore, or raise coordination.{CONTEXT_MAX_BYTES_KEY}"
        )
        self.packed_bytes = packed_bytes
        self.limit_bytes = limit_bytes


class ResolvedContext(NamedTuple):
    """A build's paths, fully resolved and known to be contained.

    Attributes:
        context: The resolved context root, which is what gets packed.
            The unresolved path would archive a symlinked context as one
            symlink entry and recurse into nothing.
        dockerfile: The Dockerfile's context-relative path, which is
            what the daemon is given.
    """

    context: Path
    dockerfile: Path


def assert_contained(dockerfile: Path, context_dir: Path) -> ResolvedContext:
    """Reject a Dockerfile that escapes the build context.

    The daemon requires the Dockerfile to live inside the build context, and a
    caller-supplied path that resolves (through symlinks) outside
    ``context_dir`` would let an attacker have a build read arbitrary host
    files. Both paths are fully resolved before the containment check so a
    symlink cannot slip the Dockerfile out of the context after validation.

    Returns:
        The resolved context root and the Dockerfile's path relative to it.

    Raises:
        EnvironmentConfigError: When ``dockerfile`` does not resolve to a path
            inside ``context_dir`` (422).
    """
    resolved_context = context_dir.resolve()
    resolved_dockerfile = dockerfile.resolve()
    if not resolved_dockerfile.is_relative_to(resolved_context):
        msg = (
            f"Dockerfile {resolved_dockerfile} is outside the build "
            f"context {resolved_context}"
        )
        raise EnvironmentConfigError(msg)
    return ResolvedContext(
        context=resolved_context,
        dockerfile=resolved_dockerfile.relative_to(resolved_context),
    )


def _excluded(relative_path: str, ignore: DockerignoreMatcher) -> bool:
    """Whether *relative_path* stays out of the build context.

    Returns:
        ``True`` for a path the ignore file excludes or that names an
        unconditionally excluded segment.
    """
    if not _ALWAYS_EXCLUDED.isdisjoint(relative_path.split("/")):
        return True
    return ignore.excludes(relative_path)


def context_tar(
    resolved: ResolvedContext,
    ignore: DockerignoreMatcher,
    limit_bytes: int,
) -> io.BytesIO:
    """Pack the build context into an in-memory gzip tar.

    Symlinks are archived as symlinks rather than followed, so a link pointing
    outside the context arrives as a dangling link inside the build rather
    than as a copy of whatever it targeted.

    Excluding a directory prunes it rather than emptying it: the walk never
    descends, so a large ignored tree costs nothing.

    Args:
        resolved: The resolved context root and relative Dockerfile.
        ignore: The context's ``.dockerignore`` rules.
        limit_bytes: Ceiling on the total uncompressed member size.

    Returns:
        A rewound gzip-tar stream of the build context.

    Raises:
        ContextTooLargeError: When the members packed so far exceed
            ``limit_bytes``. Raised while packing, so the heap never holds
            more than the ceiling's worth.
    """
    dockerfile = resolved.dockerfile.as_posix()
    packed = 0

    def _select(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        nonlocal packed
        relative = info.name.removeprefix("./")
        if relative == ".":
            return info
        if relative != dockerfile and _excluded(relative, ignore):
            return None
        packed += info.size
        if packed > limit_bytes:
            raise ContextTooLargeError(packed, limit_bytes)
        return info

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(str(resolved.context), arcname=".", recursive=True, filter=_select)
    buffer.seek(0)
    return buffer
