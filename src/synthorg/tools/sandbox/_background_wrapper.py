"""Shell text for starting, polling, reading, killing, and pinning jobs.

Covers a background job's own lifecycle (start, poll, read, kill) and a
foreground exec pinned so its timeout kill can target just its own
process group, rather than the whole container.

Pure string-building: nothing here touches Docker or the filesystem, so
every function is testable without a daemon. The one contract that
matters is that the job's own argv reaches ``bash -c`` exactly as
:func:`synthorg.tools._shell_invocation.shell_invocation` would build it
for a foreground call -- backgrounding must not change how the command
text is parsed, or a command safe in the foreground becomes unsafe (or
simply different) once backgrounded.

Output is capped at READ time (:func:`build_read_output_command`), not write time.
An earlier design piped the command's own stdout through a byte-capping
``dd``/FIFO stage so a runaway job could never grow past the cap on
disk; that route hits a real failure mode a foreground truncation never
has to consider: the writer exceeding the cap receives SIGPIPE (or, via
a ulimit, SIGXFSZ) and is killed by it, which is a job DYING at the cap
rather than being read back capped, indistinguishable from a real
crash. The write-time-cap concern (tmpfs exhaustion) is answered by the
per-owner job-count cap and each job's own ``max_duration_seconds``
ceiling instead -- a bound on how much a job CAN write, not a bound
enforced by killing it while it writes. A job's own PID is a genuine
per-command process, never a pipeline stage, because ``$!`` after a
pipeline names the LAST stage, not the command a caller might need to
kill.
"""

from pathlib import PurePosixPath
from typing import Final

from synthorg.tools._shell_invocation import SHELL_ARGS_PREFIX, SHELL_PROGRAM

#: Container-side directory a job's own files live under, scoped by job
#: id. Under ``/tmp`` (a separate tmpfs mount, never the workspace bind)
#: so a job's log droppings are structurally invisible to
#: ``workspace_fingerprint.py``'s zero-artifact scan.
JOBS_ROOT: Final[str] = "/tmp/.synthorg-jobs"  # noqa: S108

#: Polling cadence and bound for the start command's own wait for the
#: PID file. The write it waits for is a `cd` + `setsid` + `echo` away,
#: so this resolves in low single-digit milliseconds in practice; the
#: cap only exists so a pathological failure (e.g. a full tmpfs) fails
#: the exec with empty stdout after a bounded time rather than hanging
#: the caller's exec indefinitely.
_PID_POLL_INTERVAL_SECONDS: Final[float] = 0.02
_PID_POLL_MAX_ITERATIONS: Final[int] = 500


def job_dir(job_id: str) -> str:
    """Return the container-side directory for *job_id*.

    Returns:
        The POSIX directory path.
    """
    return str(PurePosixPath(JOBS_ROOT) / job_id)


def output_path(job_id: str) -> str:
    """Return the container-side captured-output file path for *job_id*.

    Returns:
        The POSIX file path.
    """
    return str(PurePosixPath(job_dir(job_id)) / "output")


def pid_path(job_id: str) -> str:
    """Return the container-side PID file path for *job_id*.

    Returns:
        The POSIX file path.
    """
    return str(PurePosixPath(job_dir(job_id)) / "pid")


def exit_code_path(job_id: str) -> str:
    """Return the container-side exit-code sentinel path for *job_id*.

    Returns:
        The POSIX file path.
    """
    return str(PurePosixPath(job_dir(job_id)) / "exit_code")


def _quote(text: str) -> str:
    """Single-quote *text* for embedding in a shell command.

    Returns:
        A shell-safe single-quoted token. Never interpolate *text* into
        a shell string any other way: this is the one escaping rule the
        rest of this module depends on.
    """
    return "'" + text.replace("'", "'\\''") + "'"


def build_start_command(
    job_id: str,
    command: str,
    *,
    container_cwd: str,
) -> tuple[str, tuple[str, ...]]:
    """Build the ``(program, args)`` that start *command* detached.

    The returned exec is fast-returning: everything up to and including
    the ``echo`` of the child PID runs in the wrapper's own foreground,
    and only the real command (plus the small subshell that waits on it
    to record its exit code) is backgrounded. The exec's own stdout is
    therefore just the child PID, which the caller parses to persist the
    job record.

    Args:
        job_id: This job's id; scopes every file it owns.
        command: The agent's own shell line, unmodified. Reaches
            ``bash -c`` exactly as it would for a foreground
            ``shell_command`` call.
        container_cwd: Working directory the real command runs in.

    Returns:
        The ``(program, args)`` pair to pass to the sandbox's attached
        exec, e.g. ``container.exec(cmd=[program, *args], ...)``.
    """
    directory = job_dir(job_id)
    out = output_path(job_id)
    pid_file = pid_path(job_id)
    exit_file = exit_code_path(job_id)
    inner = " ".join((SHELL_PROGRAM, *SHELL_ARGS_PREFIX, _quote(command)))
    # `setsid TARGET` only makes TARGET's own pid double as its process
    # group id when `setsid` execs directly into TARGET with no
    # intervening fork. Bash silently declines that exec-replacement
    # (forking a child instead, which then gets a group of its OWN,
    # divorced from the one `setsid` established) whenever the
    # would-be-replaced command carries `cd ... &&` before it or I/O
    # redirection on its own invocation -- confirmed empirically against
    # the sandbox's own base image: both shapes left the tracked pid
    # process-group-orphaned, so a later `kill -TERM -<pid>` (see
    # build_kill_command) signalled nobody. `cd`, the redirect, and the
    # real command are therefore folded into ONE setsid'd bash script
    # using explicit `exec`s: a bare `exec` never forks (that is its
    # entire defined purpose, not a heuristic bash can decline), and
    # `exec` with only redirections mutates the current process's fds in
    # place. Nothing in this chain forks, so the pid setsid assigned a
    # fresh session/group to is the exact pid that ends up running the
    # command, all the way down.
    setup = (
        f"cd {_quote(container_cwd)} || exit 1; "
        f"exec > {_quote(out)} 2>&1 < /dev/null || exit 1; "
        f"exec {inner}"
    )
    detached = " ".join((SHELL_PROGRAM, *SHELL_ARGS_PREFIX, _quote(setup)))
    # `mkdir` runs synchronously, before anything is backgrounded, so the
    # directory is guaranteed to exist before the poll loop below ever
    # looks for the PID file inside it.
    #
    # The command-plus-wait live in ONE background job (one fork): `wait`
    # can only wait on the current process's OWN children, so the process
    # that backgrounds the real command via `setsid ... &` must be the
    # same process that later `wait`s on it. A separate `( wait $pid ) &`
    # subshell is a SIBLING of that job, not its parent, and always fails
    # with "not a child of this shell".
    #
    # That background job's own `echo "$child_pid"` write is therefore
    # not observable by the exec's own (already-returned) foreground, so
    # the foreground instead polls for the PID file the job writes and
    # cats it once it appears -- the file, not a pipe, is the hand-off.
    #
    # The `{ ... }` group must not inherit this exec's own stdio: with no
    # redirection, the caller's read of this exec's stdout/stderr blocks
    # until EOF, and EOF cannot happen until every process holding those
    # descriptors closes them -- including this backgrounded group, which
    # otherwise keeps them open for the real command's entire runtime
    # (confirmed directly: an unredirected group turned an exec meant to
    # return in milliseconds into one that blocked until the job itself
    # exited). Nothing in the group writes to stdout/stderr on its own
    # (the real command's own streams are already redirected to its
    # output file inside `detached`), so redirecting them to /dev/null
    # here costs nothing.
    script = (
        f"mkdir -p {_quote(directory)}; "
        f"{{ setsid {detached} & "
        f'child_pid=$!; echo "$child_pid" > {_quote(pid_file)}; '
        f'wait "$child_pid"; echo $? > {_quote(exit_file)}; '
        f"}} < /dev/null > /dev/null 2>&1 & disown -a; "
        f"i=0; "
        f"while [ ! -s {_quote(pid_file)} ] && "
        f'[ "$i" -lt {_PID_POLL_MAX_ITERATIONS} ]; do '
        f"sleep {_PID_POLL_INTERVAL_SECONDS}; i=$((i+1)); done; "
        f"cat {_quote(pid_file)} 2>/dev/null"
    )
    return SHELL_PROGRAM, (*SHELL_ARGS_PREFIX, script)


def build_liveness_command(job_id: str) -> tuple[str, tuple[str, ...]]:
    """Build the ``(program, args)`` that check whether a job has finished.

    Prints the exit-code sentinel's content if the job has already
    finished (the sentinel-write race is over: ``wait`` observed the
    process exit and the code is on disk), otherwise prints ``RUNNING``.
    Checking the sentinel rather than the PID is deliberate: a PID can
    be recycled by an unrelated process started later in the same
    container, and a dead PID with no sentinel yet is a real, narrow
    race (the recording subshell has not finished its own write) rather
    than evidence the job never ran; either way "not exited yet" is the
    honest answer until the sentinel says otherwise.

    Args:
        job_id: The job to check.

    Returns:
        The ``(program, args)`` pair for the sandbox's attached exec.
    """
    exit_file = exit_code_path(job_id)
    script = (
        f"if [ -s {_quote(exit_file)} ]; then "
        f"cat {_quote(exit_file)}; "
        f"else "
        f'echo "RUNNING"; '
        f"fi"
    )
    return SHELL_PROGRAM, (*SHELL_ARGS_PREFIX, script)


def build_read_output_command(
    job_id: str, *, byte_cap: int
) -> tuple[str, tuple[str, ...]]:
    """Build the ``(program, args)`` that read a job's captured output.

    Truncated to the first *byte_cap* bytes (kept from the start, not
    the tail, unlike ``ShellCommandTool``'s own foreground truncation,
    which also appends a marker). The exec reads the file directly and
    returns exactly those bytes with no marker either way, so a capped
    read is not distinguishable from output that happened to end at the
    cap.

    Args:
        job_id: The job whose output to read.
        byte_cap: Maximum bytes to return.

    Returns:
        The ``(program, args)`` pair for the sandbox's attached exec.

    Raises:
        ValueError: If *byte_cap* is not positive.
    """
    if byte_cap <= 0:
        msg = f"byte_cap must be positive, got {byte_cap!r}"
        raise ValueError(msg)
    out = output_path(job_id)
    script = f"head -c {byte_cap} {_quote(out)}"
    return SHELL_PROGRAM, (*SHELL_ARGS_PREFIX, script)


def build_kill_command(
    pid: int, *, grace_seconds: float
) -> tuple[str, tuple[str, ...]]:
    """Build the ``(program, args)`` that terminate a job's process group.

    ``setsid`` made the job's own process a new session and process
    group leader, so its PID doubles as its process-group id: signalling
    the negative PID reaches the job and anything it forked, not just
    the one process. That equivalence holds only because
    :func:`build_start_command` never lets bash fork between the
    ``setsid`` call and the real command -- see its own docstring for
    why a redirection or a ``cd`` prefix reaching that invocation
    directly breaks it, silently, with this function then signalling a
    process group nothing is actually in. Sends TERM, waits
    *grace_seconds*, then KILLs
    anything still standing; both signals are best-effort (a process
    that already exited answers with an ordinary "no such process",
    swallowed here rather than surfaced, since cancelling an already-
    finished job is not an error).

    Args:
        pid: The job's own PID (also its process-group id).
        grace_seconds: How long to wait between TERM and KILL.

    Returns:
        The ``(program, args)`` pair for the sandbox's attached exec.

    Raises:
        ValueError: If *pid* is not positive.
    """
    if pid <= 0:
        msg = f"pid must be positive, got {pid!r}"
        raise ValueError(msg)
    script = (
        f"kill -TERM -{pid} 2>/dev/null; "
        f"sleep {grace_seconds}; "
        f"kill -KILL -{pid} 2>/dev/null; "
        f"true"
    )
    return SHELL_PROGRAM, (*SHELL_ARGS_PREFIX, script)


def build_pinned_exec_command(
    job_id: str,
    command: str,
    args: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    """Build the ``(program, args)`` that run an already-split argv pinned.

    Unlike :func:`build_start_command`, this never detaches and never
    redirects the real command's own stdout/stderr: the exec this
    produces is meant to be drained exactly like an ordinary foreground
    exec (same attached stream, same separate stdout/stderr, no output
    cap), so a caller sharing a container with a live background job
    gets identical streaming and exit-code semantics to an unpinned
    foreground exec, while still being able to kill just this one
    process group on timeout.

    ``setsid`` execs directly into ``bash`` (its own argv[1]), so no fork
    happens there. Inside the script, ``mkdir -p`` may fork (an ordinary,
    non-final external command; the shell that forked it is unaffected),
    ``echo $$ > pidfile`` is a builtin (no fork), and the final ``exec``
    replaces the running image in place -- so the pid written to the
    pidfile is, all the way through, the same pid ``setsid`` made into a
    process-group leader. *command*/*args* arrive as an already-built
    argv (e.g. from :func:`synthorg.tools._shell_invocation.shell_invocation`),
    not raw text, so each element is quoted individually and the whole is
    handed to ``exec`` as words -- no second parse of a command string,
    unlike :func:`build_start_command`'s own raw-text embedding.

    Args:
        job_id: Scopes this pinned exec's pid file under its own
            directory; never persisted as a real background job.
        command: The program to run (first argv element).
        args: The program's own arguments.

    Returns:
        The ``(program, args)`` pair to pass to the sandbox's attached
        exec.
    """
    directory = job_dir(job_id)
    pid_file = pid_path(job_id)
    argv = " ".join(_quote(part) for part in (command, *args))
    script = f"mkdir -p {_quote(directory)}; echo $$ > {_quote(pid_file)}; exec {argv}"
    return "setsid", (SHELL_PROGRAM, *SHELL_ARGS_PREFIX, script)


def build_read_pid_command(job_id: str) -> tuple[str, tuple[str, ...]]:
    """Build the ``(program, args)`` that read a pinned exec's recorded pid.

    Prints nothing (rather than raising) when the pidfile does not exist
    yet, so a caller reading right at the very start of the exec sees an
    empty result instead of a nonzero exit it would otherwise have to
    special-case.

    Args:
        job_id: The pinned exec's own scoping id.

    Returns:
        The ``(program, args)`` pair for the sandbox's attached exec.
    """
    pid_file = pid_path(job_id)
    script = f"cat {_quote(pid_file)} 2>/dev/null || true"
    return SHELL_PROGRAM, (*SHELL_ARGS_PREFIX, script)


__all__ = [
    "JOBS_ROOT",
    "build_kill_command",
    "build_liveness_command",
    "build_pinned_exec_command",
    "build_read_output_command",
    "build_read_pid_command",
    "build_start_command",
    "exit_code_path",
    "job_dir",
    "output_path",
    "pid_path",
]
