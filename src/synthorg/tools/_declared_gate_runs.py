# module-kind: code
"""Recognise a run of a gate command the project itself declared.

A test runner is recognised from the invoked program, because every project
runs a suite and the runners are a known set. How a project lints, formats or
checks its dependencies is not: it is the project's own decision, written into
its committed manifest by the skeleton stage, and no fixed list of programs
could hold it.

So these are recognised from the manifest instead. That is not the
model-supplied flag the sibling module refuses: a flag arrives with the call and
an agent can set it per invocation, while this is a committed file the contract
job wrote and the review gate passed. What an agent CAN do by editing it is
change which command its project's lint gate is, which is the project's business
and is exactly what the field is for; what it cannot do is claim a run of
something else was that gate, because the comparison is against the declaration
rather than against anything in the call.

Matched on the command rather than on the invoked program, because the
declaration IS a command: ``ruff check .`` and ``ruff format --check .`` share
a program and prove opposite things. Argument for argument, so ``ruff check
src`` is not evidence for a gate declared as ``ruff check .``.

Matched against the SEGMENTS of the executed line rather than against the whole
of it, for the reason :mod:`synthorg.core.shell_semantics` exists: agents
type ``cd src && ruff check .`` and ``ruff check . 2>&1 | tail``, and comparing
those to the declaration whole recognises neither. That is not a cosmetic miss
now that a declared gate BLOCKS a unit: it withholds the evidence the agent
genuinely produced, refuses the unit, and the refusal names the command it just
ran, so there is nothing in the message to act on. The declaration matches when
every segment of it appears among the executed line's segments.

The conjunction module also decides whether the exit status still speaks for
those segments at all, which is what makes the recorded ``passed`` mean
anything. ``ruff check . || true`` exits zero whatever the linter did, so it is
recognised as no gate and records nothing. The same reading is applied to the
DECLARATION when the manifest is parsed, so a gate declared that way is refused
where a human reviews it rather than silently minting green records for ever.
"""

from pathlib import Path

from synthorg.core.shell_semantics import trustworthy_segments
from synthorg.engine.errors import EnvironmentConfigError
from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME
from synthorg.engine.workspace.environment.manifest import read_manifest
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import ENVIRONMENT_PENDING_MANIFEST_UNREAD
from synthorg.persistence.code_execution_protocol import CodeExecutionPurpose

logger = get_logger(__name__)


def declared_gate_purpose(
    command: str,
    *,
    workspace_root: Path | None,
    project_id: str,
) -> CodeExecutionPurpose | None:
    """Which declared gate *command* is a run of, if any.

    Args:
        command: The command line as it was executed.
        workspace_root: Base directory projects live under, or ``None`` when
            the caller was wired without one, which recognises nothing rather
            than guessing at a workspace.
        project_id: The project whose manifest declares the gates.

    Returns:
        The gate's purpose, or ``None`` when the line matches no declaration.
    """
    if workspace_root is None:
        return None
    workspace = project_workspace_dir(workspace_root, project_id)
    if not (workspace / DEFAULT_MANIFEST_FILENAME).is_file():
        return None
    try:
        manifest = read_manifest(workspace, filename=DEFAULT_MANIFEST_FILENAME)
    except EnvironmentConfigError as exc:
        # A manifest that will not parse declares no gate anybody can run, and
        # the oracle already refuses to complete a unit whose project has one:
        # recognising nothing here withholds evidence for a gate that is not
        # currently askable, rather than inventing a purpose from a broken file.
        #
        # Logged, because this runs after every command an agent executes: a
        # broken manifest silently defeats gate recognition for the whole
        # session, and without a line here the only trace is a different
        # module's read of the same file at some later verdict.
        logger.warning(
            ENVIRONMENT_PENDING_MANIFEST_UNREAD,
            project_id=project_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    ran = trustworthy_segments(command)
    if ran is None:
        return None
    for purpose, declared in manifest.declared_gates.items():
        wanted = trustworthy_segments(declared)
        if wanted is not None and wanted <= ran:
            return purpose
    return None


__all__ = ["declared_gate_purpose"]
