"""Moving a ref between two local repositories without forking a shell.

git's local transport is unusable in a shell-free image, so every
repository-to-repository hop the embedded backend makes (seed, merge,
fetch-back) goes through a bundle instead. That substitution is one idea
with one reason, kept apart from the general subprocess helpers it is
built on.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from synthorg.engine.errors import GitBackendError
from synthorg.engine.workspace._git_subprocess import (
    describe_git_failure,
    run_git_subprocess,
)
from synthorg.engine.workspace.git_backend._git_ops import git
from synthorg.observability import get_logger

logger = get_logger(__name__)

#: A ref beginning with a dash is read by git as an option, not a name. No
#: caller can produce one today (every branch name is system-built and
#: `git_worktree._validate_git_ref` already refuses the shape), but this
#: module's whole subject is a transport that must not be talked into doing
#: something else, and relying on every future caller to have checked first
#: is the assumption that stops being true quietly.
_OPTION_PREFIX: Final[str] = "-"

#: The ONE exit status `rev-parse --verify --quiet` uses to say a ref is not
#: there. Every other non-zero status is git failing for a different reason:
#: 128 covers a repository that does not exist and a revision it cannot parse,
#: and reading those as absence sends a full-history bundle nobody asked for
#: and defers the real error to the fetch, where it surfaces as something else
#: entirely.
_REV_PARSE_ABSENT: Final[int] = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class GitFailure:
    """How a git helper should report a failure.

    The three travel together at every call site already; naming them once
    keeps a helper's own arguments about the work it does.

    Keyword-only by construction. ``project_id`` and ``event`` are adjacent
    and both ``str``, so a positional swap type-checks cleanly and surfaces
    only as a log line naming an event where the project should be, which is
    exactly the kind of thing nobody reads closely enough to catch.

    Attributes:
        exc: Exception type raised on failure.
        project_id: Project identifier for structured logging.
        event: Structured-log event constant for failures.
    """

    exc: type[GitBackendError]
    project_id: str
    event: str


def _reject_option_like(ref: str) -> None:
    """Refuse a ref git would read as an option rather than a name.

    Args:
        ref: The ref about to be interpolated into a revision range or a
            refspec.

    Raises:
        ValueError: When *ref* opens with a dash.
    """
    if ref.startswith(_OPTION_PREFIX):
        msg = f"refusing option-like git ref {ref!r}"
        raise ValueError(msg)


async def _ref_sha(
    git_dir: Path,
    ref: str,
    *,
    cmd_timeout: float,
    failure: GitFailure,
) -> str | None:
    """Return the sha *ref* points at in the repository at *git_dir*.

    Args:
        git_dir: A repository's git directory (bare or ``.git``).
        ref: The ref to resolve.
        cmd_timeout: Maximum seconds the subprocess may run.
        failure: How to report git failing to run at all.

    Returns:
        The resolved sha, or ``None`` when the ref genuinely does not exist
        there, which git reports with exactly one exit status.

    Raises:
        GitBackendError: ``failure.exc`` for every other failure. Absence is
            the narrow case and everything else is an error: a negative code
            is this helper's own sentinel for a timeout, a spawn failure or a
            missing binary, and a positive code other than
            :data:`_REV_PARSE_ABSENT` is git refusing the repository or the
            revision. Reading any of them as "no such ref" sends a
            full-history bundle nobody asked for and defers the real failure
            to the fetch, where it surfaces as a different error entirely.
    """
    rc, stdout, _ = await run_git_subprocess(
        git_dir,
        f"--git-dir={git_dir}",
        "rev-parse",
        "--verify",
        "--quiet",
        f"{ref}^{{commit}}",
        cmd_timeout=cmd_timeout,
        log_event=failure.event,
    )
    if rc == _REV_PARSE_ABSENT:
        return None
    if rc != 0:
        cause = (
            describe_git_failure(rc)
            if rc < 0
            else f"git exited {rc} without reporting an absent ref"
        )
        msg = f"git could not resolve {ref!r} in {git_dir!s}: {cause}"
        logger.warning(
            failure.event,
            project_id=failure.project_id,
            ref=ref,
            return_code=rc,
            cause=cause,
        )
        raise failure.exc(msg)
    resolved = stdout.strip()
    if not resolved:
        # Exit 0 with nothing on stdout is not a shape git produces for this
        # invocation, so it means the command was not the one intended.
        msg = f"git resolved {ref!r} in {git_dir!s} to an empty revision"
        logger.warning(
            failure.event,
            project_id=failure.project_id,
            ref=ref,
            return_code=rc,
            cause="empty revision on a successful rev-parse",
        )
        raise failure.exc(msg)
    return resolved


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
    metacharacters, so ``run-command`` routes it through ``/bin/sh``. A
    distroless backend ships no shell, and git reports the pair::

        fatal: cannot exec 'git-receive-pack '<path>'': No such file or directory
        fatal: unable to fork

    Every escape is equally blocked, because the quoting is unconditional:
    a ``file://`` URL, an absolute ``--receive-pack``, and ``clone --local``
    all fail identically. It is not a missing flag, and no configuration
    reaches it.

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
    _reject_option_like(source_ref)
    _reject_option_like(target_ref)
    existing = (
        None
        if force
        else await _ref_sha(
            target_git_dir,
            target_ref,
            cmd_timeout=cmd_timeout,
            failure=failure,
        )
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
