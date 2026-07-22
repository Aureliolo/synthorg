# module-kind: code
"""Stamp a scoreboard with what it was measured against.

Loop-completion semantics are still moving, so a scoreboard recorded against an
older commit can describe behaviour the loops no longer have. Recording the
commit (and whether the tree was dirty) makes that visible in the artifact
rather than leaving a stale ranking looking authoritative.

The git lookup fails loud. A scoreboard that cannot say which commit produced it
is not reproducible, and reproducibility is an acceptance criterion here rather
than a nicety, so an unknown commit is refused instead of defaulted.
"""

import hashlib
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from evals.errors import ProvenanceUnavailableError
from evals.loop_ab.models import Provenance
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import EVALS_LOOP_AB_DIRTY_TREE

logger = get_logger(__name__)

#: Timeout for the git metadata lookups. These are local, so anything slower
#: than this means git is wedged and the recording should fail rather than hang.
_GIT_TIMEOUT_SECONDS = 30


def _git(*args: str, repo_root: Path) -> str:
    """Run a read-only git command in *repo_root* and return its stdout.

    Returns:
        The command's stripped stdout.

    Raises:
        ProvenanceUnavailableError: git is absent, failed, or timed out.
    """
    # Resolved to an absolute path rather than relying on PATH lookup at spawn
    # time, so the binary this runs is pinned at the point of decision.
    git_binary = shutil.which("git")
    if git_binary is None:
        msg = (
            "git is not on PATH, so a scoreboard cannot name the commit it "
            "measured; an unreproducible scoreboard is refused"
        )
        raise ProvenanceUnavailableError(msg)
    try:
        completed = subprocess.run(  # noqa: S603 -- absolute binary, fixed argv, no shell
            [git_binary, *args],
            cwd=repo_root,
            capture_output=True,
            check=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            shell=False,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        NotADirectoryError,
        PermissionError,
    ) as exc:
        msg = (
            f"cannot read git provenance ({' '.join(args)}) in {repo_root}: "
            f"{type(exc).__name__}: {safe_error_description(exc)}; a scoreboard "
            "that cannot name its commit is not reproducible"
        )
        raise ProvenanceUnavailableError(msg) from exc
    return completed.stdout.decode("utf-8", errors="replace").strip()


def manifest_digest(manifest_path: Path) -> str:
    """Hash the recording manifest so a changed matrix is visible in the diff.

    Returns:
        A ``sha256:``-prefixed digest of the manifest bytes.
    """
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def capture_provenance(
    *, repo_root: Path, manifest_path: Path, brief_suite_version: str
) -> Provenance:
    """Capture what this recording ran against.

    Args:
        repo_root: Repository the loops under test were measured from.
        manifest_path: The recording manifest driving the matrix.
        brief_suite_version: Stable digest of the brief suite measured.

    Returns:
        The assembled :class:`Provenance`.

    Raises:
        ProvenanceUnavailableError: The git metadata could not be read.
    """
    commit = _git("rev-parse", "HEAD", repo_root=repo_root)
    dirty = bool(_git("status", "--porcelain", repo_root=repo_root))
    if dirty:
        # Not fatal: a maintainer may deliberately record an in-progress change.
        # It is recorded so the reader knows the commit alone does not fully
        # describe the tree that was measured.
        logger.warning(EVALS_LOOP_AB_DIRTY_TREE, git_commit=commit)
    return Provenance(
        generated_at=datetime.now(UTC),
        git_commit=NotBlankStr(commit),
        git_dirty=dirty,
        manifest_sha256=NotBlankStr(manifest_digest(manifest_path)),
        brief_suite_version=NotBlankStr(brief_suite_version),
    )


__all__ = ["capture_provenance", "manifest_digest"]
