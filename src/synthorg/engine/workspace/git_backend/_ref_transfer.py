"""Moving a ref between two local repositories without forking a shell.

git's local transport is unusable in a shell-free image, so every
repository-to-repository hop the embedded backend makes (seed, merge,
fetch-back) goes through a bundle instead. That substitution is one idea
with one reason, kept apart from the general subprocess helpers it is
built on.
"""

import tempfile
from pathlib import Path
from typing import NamedTuple

from synthorg.engine.errors import GitBackendError
from synthorg.engine.workspace._git_subprocess import run_git_subprocess
from synthorg.engine.workspace.git_backend._git_ops import git
from synthorg.observability.events.workspace import GIT_BACKEND_PROVISION_FAILED


class GitFailure(NamedTuple):
    """How a git helper should report a failure.

    The three travel together at every call site already; naming them once
    keeps a helper's own arguments about the work it does.

    Attributes:
        exc: Exception type raised on failure.
        project_id: Project identifier for structured logging.
        event: Structured-log event constant for failures.
    """

    exc: type[GitBackendError]
    project_id: str
    event: str


async def _ref_sha(
    git_dir: Path,
    ref: str,
    *,
    cmd_timeout: float,
) -> str | None:
    """Return the sha *ref* points at in the repository at *git_dir*.

    Args:
        git_dir: A repository's git directory (bare or ``.git``).
        ref: The ref to resolve.
        cmd_timeout: Maximum seconds the subprocess may run.

    Returns:
        The resolved sha, or ``None`` when the ref does not exist there.
    """
    rc, stdout, _ = await run_git_subprocess(
        git_dir,
        f"--git-dir={git_dir}",
        "rev-parse",
        "--verify",
        "--quiet",
        f"{ref}^{{commit}}",
        cmd_timeout=cmd_timeout,
        log_event=GIT_BACKEND_PROVISION_FAILED,
    )
    return stdout.strip() if rc == 0 and stdout.strip() else None


async def transfer_ref_local(
    *,
    source_root: Path,
    target_git_dir: Path,
    source_ref: str,
    target_ref: str,
    force: bool = False,
    cmd_timeout: float,
    failure: GitFailure,
) -> None:
    """Move *source_ref* into *target_git_dir* without forking a shell.

    Asked to reach a repository by path, git builds one command string,
    ``git-receive-pack '<path>'``, whose space and quotes are shell
    metacharacters, so ``run-command`` routes it through ``/bin/sh`` and a
    distroless backend answers ``unable to fork``. Every escape is equally
    blocked, because the quoting is unconditional: a ``file://`` URL, an
    absolute ``--receive-pack``, and ``clone --local`` all fail
    identically. It is not a missing flag, and no configuration reaches
    it.

    A bundle does the same job over a path git opens directly rather than
    a helper it must spawn, so the transfer needs no shell at all. The
    bundle carries only what the target lacks when the target already has
    the ref, because a project's history outlives the wave that appends
    to it and re-packing all of it per merge would grow without bound.

    Args:
        source_root: Working directory of the repository holding the ref.
        target_git_dir: Git directory receiving it (a bare repo, or a
            working tree's ``.git``).
        source_ref: The ref to read in *source_root*.
        target_ref: The fully-qualified ref to write in the target.
        force: Whether the update may discard history the target has.
            Seeding needs it: provisioning wrote a throwaway empty commit
            that the imported history is unrelated to.
        cmd_timeout: Maximum seconds any one subprocess may run.
        failure: How to report a failure.

    Raises:
        GitBackendError: ``failure.exc`` when the bundle or the fetch fails.
    """
    existing = (
        None
        if force
        else await _ref_sha(target_git_dir, target_ref, cmd_timeout=cmd_timeout)
    )
    head = await git(
        source_root,
        "rev-parse",
        "--verify",
        f"{source_ref}^{{commit}}",
        cmd_timeout=cmd_timeout,
        fail_exc=failure.exc,
        project_id=failure.project_id,
        event=failure.event,
    )
    if existing == head:
        # Already there. `git bundle create` refuses an empty range, so
        # asking for one would turn a no-op into a failed transfer.
        return
    revision = f"{existing}..{source_ref}" if existing is not None else source_ref
    with tempfile.TemporaryDirectory() as tmp_dir:
        bundle = Path(tmp_dir) / "transfer.bundle"
        await git(
            source_root,
            "bundle",
            "create",
            str(bundle),
            revision,
            cmd_timeout=cmd_timeout,
            fail_exc=failure.exc,
            project_id=failure.project_id,
            event=failure.event,
        )
        refspec = (
            f"+{source_ref}:{target_ref}" if force else f"{source_ref}:{target_ref}"
        )
        await git(
            target_git_dir,
            f"--git-dir={target_git_dir}",
            "fetch",
            str(bundle),
            refspec,
            cmd_timeout=cmd_timeout,
            fail_exc=failure.exc,
            project_id=failure.project_id,
            event=failure.event,
        )
