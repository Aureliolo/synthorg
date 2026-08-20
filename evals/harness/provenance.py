# module-kind: code
"""What a recording ran against: the commit, the tree state, the manifest.

A recorded artifact that cannot name the commit it measured is not
reproducible, and reproducibility is an acceptance criterion for every artifact
this spine produces rather than a nicety. So the git lookup fails loud: an
unknown commit is refused, never defaulted.

The artifact model itself stays with whichever harness owns it. Two harnesses
record different things and stamp different fields; what they share is how the
facts are obtained, which is all that lives here.
"""

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from evals.errors import ProvenanceUnavailableError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import EVALS_HARNESS_DIRTY_TREE

logger = get_logger(__name__)

#: Timeout for the git metadata lookups. These are local, so anything slower
#: than this means git is wedged and the recording should fail rather than hang.
_GIT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class GitState:
    """The tree a recording was measured from.

    Attributes:
        commit: The full commit SHA at ``HEAD``.
        dirty: Whether the working tree carried uncommitted changes, in which
            case the commit alone does not describe what ran.
    """

    commit: str
    dirty: bool


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
            "git is not on PATH, so a recording cannot name the commit it "
            "measured; an unreproducible artifact is refused"
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
            f"{type(exc).__name__}: {safe_error_description(exc)}; an artifact "
            "that cannot name its commit is not reproducible"
        )
        raise ProvenanceUnavailableError(msg) from exc
    return completed.stdout.decode("utf-8", errors="replace").strip()


def manifest_digest(manifest_path: Path) -> str:
    """Hash a recording manifest so a changed matrix is visible in the diff.

    Returns:
        A ``sha256:``-prefixed digest of the manifest bytes.
    """
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def capture_git_state(repo_root: Path) -> GitState:
    """Read the commit and tree state a recording is being made from.

    Args:
        repo_root: Repository the thing under test was measured from.

    Returns:
        The :class:`GitState`.

    Raises:
        ProvenanceUnavailableError: The git metadata could not be read.
    """
    commit = _git("rev-parse", "HEAD", repo_root=repo_root)
    dirty = bool(_git("status", "--porcelain", repo_root=repo_root))
    if dirty:
        # Not fatal: a maintainer may deliberately record an in-progress change.
        # It is recorded so the reader knows the commit alone does not fully
        # describe the tree that was measured.
        logger.warning(EVALS_HARNESS_DIRTY_TREE, git_commit=commit)
    return GitState(commit=commit, dirty=dirty)


__all__ = ["GitState", "capture_git_state", "manifest_digest"]
