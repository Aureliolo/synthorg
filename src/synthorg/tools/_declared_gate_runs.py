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

Matched on the whole normalised line rather than the invoked program, because
the declaration IS a line: ``ruff check .`` and ``ruff format --check .`` share
a program and prove opposite things. Whitespace is normalised so a line an agent
re-spaced still matches; nothing else is, so ``ruff check src`` is not evidence
for a gate declared as ``ruff check .``.
"""

from pathlib import Path

from synthorg.engine.errors import EnvironmentConfigError
from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME
from synthorg.engine.workspace.environment.manifest import read_manifest
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.persistence.code_execution_protocol import CodeExecutionPurpose


def _normalised(command: str) -> str:
    """Reduce a command line to what two spellings of it can be compared on.

    Returns:
        The line with runs of whitespace collapsed and the ends trimmed.
    """
    return " ".join(command.split())


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
    except EnvironmentConfigError:
        # A manifest that will not parse declares no gate anybody can run, and
        # the oracle already refuses to complete a unit whose project has one:
        # recognising nothing here withholds evidence for a gate that is not
        # currently askable, rather than inventing a purpose from a broken file.
        return None
    line = _normalised(command)
    for purpose, declared in manifest.declared_gates.items():
        if _normalised(declared) == line:
            return purpose
    return None


__all__ = ["declared_gate_purpose"]
