# module-kind: code
"""How a shell line is invoked, so its exit status means something.

Every agent shell line runs through here, and it runs with ``pipefail``
because the build/test oracle reads exit statuses as evidence. Without it a
pipeline reports only its LAST stage: ``npm test | tail -12`` exits 0 with a
suite that failed, so a status nobody can trust is a status the classifier
must refuse, and refusing it is why a live run produced 181 shell commands,
several genuinely green suites, and zero test evidence.

With ``pipefail`` a zero status from a line built only of ``&&`` and ``|``
proves every command in it exited zero. That theorem is what
:mod:`synthorg.tools._test_run_capture` relies on to accept the shapes
agents actually type, so the two travel together: change the invocation and
the classifier's premise changes with it.
"""

from typing import Final

#: The shell every agent command line is executed by.
SHELL_PROGRAM: Final[str] = "bash"

#: Arguments preceding the command string. ``-o pipefail`` makes a
#: pipeline's status the first failure rather than the last stage's.
SHELL_ARGS_PREFIX: Final[tuple[str, ...]] = ("-o", "pipefail", "-c")


def shell_invocation(command: str) -> tuple[str, tuple[str, ...]]:
    """Return the (program, args) that run *command* in a shell.

    Args:
        command: The shell line to run.

    Returns:
        The program name and its full argument tuple.
    """
    return SHELL_PROGRAM, (*SHELL_ARGS_PREFIX, command)


__all__ = ["SHELL_ARGS_PREFIX", "SHELL_PROGRAM", "shell_invocation"]
