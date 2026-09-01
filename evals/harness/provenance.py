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
from collections.abc import Sequence
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


def _dirty_argv(repo_root: Path, ignoring: Sequence[Path]) -> tuple[str, ...]:
    """The status query that asks whether the CODE under test was edited.

    A recorder writes its report into a directory it was given, and both
    harnesses here default that to a TRACKED one, because the artifact is
    committed. So a recording that finishes turns the tree dirty by its own
    output, and since ``dirty`` is part of the resume identity, the next
    ``--resume`` is refused and every cell already paid for is forfeit. What the
    flag is meant to say is that the commit does not fully describe the code
    that ran, and the recorder's own output is not that.

    Takes a SEQUENCE, because a CONCURRENT SIBLING's output is equally not that
    and excluding only the caller's own directory does not cover it. Measured on
    three cells run concurrently, which the recursion-depth plan explicitly
    endorses: the first read a clean tree and the other two read dirty, on the
    same commit, solely because the first cell's directory had appeared. The
    consequence is the one this exclusion exists to prevent, displaced onto a
    sibling, and it had already happened: the clean cell could no longer be
    resumed.

    Excluded by pathspec rather than by filtering the output, so git does the
    matching: a directory outside the repository, or none at all, excludes
    nothing and the query is the plain one. The REPOSITORY ROOT is the same
    case for the opposite reason: ``:(exclude).`` matches every tracked path,
    so a recording writing to ``--out-dir .`` would read clean however much
    source it had edited, and a resume would then mix two source states under
    one provenance record.

    Rendered with forward slashes because a pathspec is git's syntax rather than
    the platform's, and a Windows separator inside ``:(exclude)`` matches
    nothing at all: the exclusion would silently do nothing on the one platform
    this is recorded from.

    Args:
        repo_root: Repository the thing under test was measured from.
        ignoring: Directories recordings write into. Empty excludes nothing.

    Returns:
        The argv after ``git``.
    """
    excludes: list[str] = []
    for directory in ignoring:
        try:
            relative = directory.resolve().relative_to(repo_root.resolve())
        except ValueError:
            # Outside the repository, so nothing it holds was ever going to
            # appear in the status output at all.
            continue
        if relative == Path():
            # ``:(exclude).`` matches every tracked path, so one such entry
            # would blind the whole query. Dropping it rather than returning
            # early keeps a genuine sibling exclusion in the same call.
            continue
        excludes.append(f":(exclude){relative.as_posix()}")
    if not excludes:
        return ("status", "--porcelain")
    return ("status", "--porcelain", "--", ".", *excludes)


def capture_git_state(repo_root: Path, *, ignoring: Sequence[Path] = ()) -> GitState:
    """Read the commit and tree state a recording is being made from.

    Args:
        repo_root: Repository the thing under test was measured from.
        ignoring: Directories recordings write their own artifacts into, which
            are excluded from the dirty check. A concurrent sibling's output
            belongs here as much as this recording's own: see
            :func:`_dirty_argv`.

    Returns:
        The :class:`GitState`.

    Raises:
        ProvenanceUnavailableError: The git metadata could not be read.
    """
    commit = _git("rev-parse", "HEAD", repo_root=repo_root)
    dirty = bool(_git(*_dirty_argv(repo_root, ignoring), repo_root=repo_root))
    if dirty:
        # Not fatal: a maintainer may deliberately record an in-progress change.
        # It is recorded so the reader knows the commit alone does not fully
        # describe the tree that was measured.
        logger.warning(EVALS_HARNESS_DIRTY_TREE, git_commit=commit)
    return GitState(commit=commit, dirty=dirty)


__all__ = ["GitState", "capture_git_state", "manifest_digest"]
