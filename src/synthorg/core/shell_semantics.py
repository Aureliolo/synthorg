# module-kind: code
"""Does a command line's exit status still speak for the commands inside it.

Shell semantics, kept apart from the question of what a command IS. A line is
split into the commands its own status vouches for, and refused outright when
any part of it breaks that implication, so a caller can ask "did this run a test
suite" of each command without re-deriving whether the answer would mean
anything.

``pytest || true`` and ``pytest; echo done`` both exit 0 whatever the suite did,
so they are refused: a status recorded for them would describe the tail rather
than the tests.

``&&`` and ``|`` are different, and refusing them cost the caller everything it
was for. A line built only of those two exits zero only when EVERY command in it
exited zero: ``&&`` short-circuits by definition, and ``|`` does the same because
:mod:`synthorg.tools._shell_invocation` runs every agent line under ``pipefail``.
So ``cd /workspace && npm test 2>&1 | tail`` is exactly as trustworthy as a bare
``npm test``, and it is the shape agents actually type. Refusing it meant a live
run produced 181 shell commands, several genuinely green suites, and zero
evidence, and the oracle correctly blocked every one of them for a build that
passed.

That theorem is about the shell WE start, so it stops at the first shell the line
starts itself: ``pipefail`` is a shell option and a fresh shell does not inherit
it. Inside a ``bash -c`` payload a pipeline is therefore back to reporting its
last command's status, and ``|`` is refused there.

Redirections are noise: they move file descriptors and leave the exit status
alone. Command substitution, backgrounding and subshells are refused, since each
can run a program the parse never sees. A statement separator is refused against
the raw line rather than the token stream, because :mod:`shlex` lists newline in
its whitespace and hands back tokens with the separator already eaten: a line
running ``pytest -q``, then a newline, then ``echo ok`` would otherwise read as
one command headed by the runner, while the status recorded for it is the one
``echo`` exited with.
"""

import shlex
from collections.abc import Sequence
from pathlib import PurePosixPath, PureWindowsPath
from typing import Final

#: Told to the agent up front, on the tool that runs the line, and again by
#: the oracle when no run was recorded: an agent that learns the shape only
#: from the refusal has already spent the turn, and a live one spent three
#: rework rounds that way.
RECORDED_RUN_RULE: Final[str] = (
    "A test run is evidence only when the line's exit status is the runner's "
    "own: `pytest -q`, or a `&&` chain or pipeline ending in it, never "
    "`pytest -q; echo $?`."
)

#: Shells whose ``-c`` argument is itself a command line. Its trustworthiness is
#: a separate question from the outer line's, because ``pipefail`` is a shell
#: option and a shell this line starts does not inherit ours.
_SHELLS: Final[frozenset[str]] = frozenset({"bash", "sh", "zsh", "dash", "ash"})
_SHELL_COMMAND_FLAG: Final[str] = "-c"
#: ``<shell> -c <one command string>`` and nothing else.
_SHELL_INVOCATION_TOKENS: Final[int] = 3

#: Operators joining commands whose statuses the line's status still
#: implies: ``&&`` short-circuits, and ``|`` is conjunctive under the
#: ``pipefail`` every agent line runs with.
_CONJUNCTIVE_SEPARATORS: Final[frozenset[str]] = frozenset({"&&", "|"})

#: Operators that make the line's exit status stop being the runner's own
#: (``;``, ``||``, backgrounding) or that run a program the parse never
#: sees (subshells, substitution).
_STATUS_MASKING_TOKENS: Final[frozenset[str]] = frozenset(
    {";", ";;", "||", "&", "|&", "(", ")", "$", "{", "}"}
)

#: Redirection operators. They move file descriptors and leave the exit
#: status alone, so both the operator and its target are dropped.
_REDIRECTIONS: Final[frozenset[str]] = frozenset(
    {">", ">>", ">|", ">&", "<", "<<", "<<<", "<&", "&>", "&>>"}
)

#: Characters no token may contain. A backtick runs a command the parse
#: never sees. Statement separators are NOT here: :mod:`shlex` lists them
#: in ``whitespace``, so it consumes them as token boundaries and no token
#: can ever hold one. They are checked against the raw line instead, by
#: :data:`_STATEMENT_SEPARATORS`.
_FORBIDDEN_IN_TOKEN: Final[tuple[str, ...]] = ("`",)

#: Characters that end a statement, checked against the unlexed line.
#: A second statement's exit status is the line's, so ``pytest -q\necho ok``
#: reports the status of ``echo``: the runner could have failed and the
#: line still exits zero, which is a passing record for a red suite.
_STATEMENT_SEPARATORS: Final[tuple[str, ...]] = ("\n", "\r")

#: The pipe, whose conjunctive reading holds only under ``pipefail``.
_PIPE: Final[str] = "|"

#: The builtin that toggles ``pipefail``, and the signs that do it.
#: ``set -o`` enables, ``set +o`` disables, which is the opposite of the
#: convention most flags follow.
_SET_BUILTIN: Final[str] = "set"
_UNSET_SIGN: Final[str] = "+"
_SET_SIGN: Final[str] = "-"
#: The letter ``-o`` / ``+o`` ends with. Read as the LAST character of the
#: token rather than the whole token, because a shell bundles short flags:
#: ``set -euo pipefail`` is one token ``-euo`` whose trailing ``o`` takes
#: ``pipefail`` as its argument, exactly as a lone ``-o`` would.
_OPTION_FLAG: Final[str] = "o"
_PIPEFAIL_OPTION: Final[str] = "pipefail"


def _pipefail_toggle(command: Sequence[str]) -> bool | None:
    """Read a ``set`` builtin's effect on ``pipefail``.

    Both directions, not just the disable. Tracking only ``set +o`` makes the
    option a one-way latch: a line that turns it off and back on before its
    pipeline is refused, and refusing a line whose pipeline IS protected
    withholds the evidence a genuine test run produced, which is the failure
    this module's whole conjunctive reading exists to avoid.

    The flag is matched on its shape rather than against ``-o`` and ``+o``
    literally, because ``set -euo pipefail`` is the ordinary way to write this
    line and bundles the option letter into one token. Reading only the exact
    spellings answers "says nothing" for it, so ``set +eo pipefail`` before a
    pipe leaves the option believed ON while the shell has turned it OFF, and
    a pipeline whose exit status is its last command's is then read as
    evidence its first command passed.

    Returns:
        ``True`` when the command enables ``pipefail``, ``False`` when it
        disables it, and ``None`` when it says nothing about it.
    """
    if not command or command[0] != _SET_BUILTIN:
        return None
    # A single command can carry both (``set +o errexit -o pipefail``), so the
    # answer is the flag immediately preceding each option name rather than
    # whichever flag appears anywhere in the line. It can also name the option
    # twice (``set -o pipefail +o pipefail``), and the shell applies them in
    # order, so the LAST one is the state the command leaves behind: reading
    # the first inverts the answer on exactly that line.
    state: bool | None = None
    for index, token in enumerate(command):
        if index == 0 or token != _PIPEFAIL_OPTION:
            continue
        preceding = command[index - 1]
        if not preceding.endswith(_OPTION_FLAG):
            continue
        if preceding.startswith(_SET_SIGN):
            state = True
        elif preceding.startswith(_UNSET_SIGN):
            state = False
    return state


def conjunctive_commands(
    command: str, *, pipefail: bool
) -> tuple[tuple[str, ...], ...] | None:
    """Split *command* into the commands its exit status speaks for.

    Args:
        command: The full command line as it was executed.
        pipefail: Whether the shell running this line has ``pipefail`` set.
            Without it a pipeline's status is its LAST command's, so ``|``
            stops being conjunctive and the line proves nothing about the
            runner to its left.

    Returns:
        The argv of every command in the line when a zero exit status
        proves each of them exited zero, or ``None`` when any part of the
        line breaks that implication.
    """
    if any(char in command for char in _STATEMENT_SEPARATORS):
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None

    segments: list[tuple[str, ...]] = []
    current: list[str] = []
    preceding_separator: str | None = None
    skip_target = False
    for token in tokens:
        if skip_target:
            skip_target = False
            continue
        if any(char in token for char in _FORBIDDEN_IN_TOKEN):
            return None
        if token in _STATUS_MASKING_TOKENS:
            return None
        if token in _REDIRECTIONS:
            # The descriptor number preceding the operator is part of the
            # redirection, not an argument: ``npm test 2>&1`` lexes as
            # ``npm test 2 >& 1``.
            if current and current[-1].isdigit():
                current.pop()
            skip_target = True
            continue
        if token in _CONJUNCTIVE_SEPARATORS:
            if token == _PIPE and not pipefail:
                return None
            if current:
                # A line may revoke the option the pipe's trustworthiness
                # rests on: after ``set +o pipefail`` a pipeline reports its
                # LAST command's status again, so ``pytest | tail`` exits 0
                # whatever the suite did. Read per segment rather than once
                # up front, because the toggle and the pipeline are separate
                # commands and only a pipe AFTER the toggle is affected.
                #
                # A toggle only reaches the shell running the LINE when it
                # ran there itself. Every component of a pipeline runs in a
                # subshell, so ``set +o pipefail | cat`` changes that
                # subshell and exits, leaving the line's own option untouched
                # and a later pipeline still protected. Persisting it would
                # refuse the evidence that later pipeline legitimately
                # produced.
                in_pipeline = _PIPE in (token, preceding_separator)
                toggled = None if in_pipeline else _pipefail_toggle(current)
                if toggled is not None:
                    pipefail = toggled
                segments.append(tuple(current))
            preceding_separator = token
            current = []
            continue
        current.append(token)
    if current:
        segments.append(tuple(current))
    return tuple(segments)


def program_name(token: str) -> str:
    """Reduce an argv head to the bare program name.

    Returns:
        The lowercased basename with any ``.exe`` suffix removed, so an
        absolute or Windows-style path resolves to the same name a bare
        invocation would.
    """
    name = PurePosixPath(PureWindowsPath(token).name).name.lower()
    return name.removesuffix(".exe")


def shell_payload(argv: Sequence[str]) -> str | None:
    """The command line a ``<shell> -c <payload>`` invocation runs, if it is one.

    Args:
        argv: One command's argv, as :func:`conjunctive_commands` split it.

    Returns:
        The payload, or ``None`` when this argv is not that exact shape.
    """
    if len(argv) != _SHELL_INVOCATION_TOKENS:
        return None
    if program_name(argv[0]) not in _SHELLS or argv[1] != _SHELL_COMMAND_FLAG:
        return None
    return argv[2]


def _starts_a_shell(argv: Sequence[str]) -> bool:
    """Whether *argv* hands a command line to a shell it starts.

    Returns:
        Whether the head is a shell and the argv carries ``-c`` at all, which
        is broader than :func:`shell_payload` on purpose: it is the question
        "is there a payload here", asked so a shape this module cannot read
        can be refused rather than trusted whole.
    """
    if not argv or program_name(argv[0]) not in _SHELLS:
        return False
    return _SHELL_COMMAND_FLAG in argv


def trustworthy_segments(command: str) -> frozenset[tuple[str, ...]] | None:
    """Split *command* into the commands its exit status still speaks for.

    The set form of :func:`conjunctive_commands`, for callers comparing one
    line against another rather than walking a line in order. Shared between
    the manifest that DECLARES a gate command and the capture path that
    recognises a run of it, so the reading applied to a declaration is
    character for character the one applied to the run claimed against it. Two
    readings would mean a gate a project may declare but never satisfy, or the
    reverse.

    A ``-c`` payload is descended into, once, because the outer parse sees it
    as a single quoted token: ``bash -c 'ruff check . || true'`` otherwise
    reads as one trustworthy command whose zero exit vouches for a linter that
    failed. The nested line is parsed with ``pipefail=False``, since the shell
    running it is one this line just started and the option does not cross that
    boundary. Anything that still invokes a shell after that descent is refused
    rather than returned, because a second level is a payload this module did
    not read and a segment nobody read cannot vouch for anything.

    Args:
        command: The command line, as declared or as executed.

    Returns:
        Every command in the line as its argv, or ``None`` when any part of the
        line breaks the implication that a zero exit means each of them exited
        zero, or when the line runs no command at all.
    """
    segments = conjunctive_commands(command, pipefail=True)
    if segments is None:
        return None
    expanded: list[tuple[str, ...]] = []
    for segment in segments:
        payload = shell_payload(segment)
        if payload is None:
            if _starts_a_shell(segment):
                return None
            expanded.append(segment)
            continue
        nested = conjunctive_commands(payload, pipefail=False)
        if nested is None or any(_starts_a_shell(inner) for inner in nested):
            return None
        expanded.extend(nested)
    if not expanded:
        # A line that lexes to nothing runs nothing, and the empty set is a
        # subset of every other, so the caller's ``wanted <= ran`` test passes
        # against ANY run. ``# deferred`` as a declared gate would then mint a
        # passing receipt off whatever the agent happened to type, and an empty
        # ``-c`` payload does the same one level down. Both sides of that
        # comparison come through here, so refusing once covers a declaration
        # that runs nothing and a run that does.
        return None
    return frozenset(expanded)


__all__ = [
    "conjunctive_commands",
    "program_name",
    "shell_payload",
    "trustworthy_segments",
]
