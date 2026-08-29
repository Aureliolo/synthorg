# module-kind: code
"""What a project's pending declaration says about one task's test run.

A skeleton commits one test per acceptance criterion before anything implements
the contract, so a CORRECT skeleton's suite exits non-zero. Read as evidence,
that run blocks the very deliverable the contract stage exists to produce.

The correction belongs here rather than on the record. ``CodeExecutionRecord``
answers "did this command exit zero", enforced by a validator and a database
CHECK, and that is a measurement worth keeping honest: a caller that could write
``passed`` freely could mint a green build from any run at all. The oracle is
what turns measurements into a verdict, so the declaration is read here, where
the verdict already lives, and the row keeps saying what actually happened.

Two questions, because the exit status can answer neither and they point in
opposite directions.

**Was this failure declared?** A run whose pending tests all failed their own
declared assertions, with nothing else broken, is forgiven. Any other pending
outcome, or an ordinary test broken alongside them, is not. One bit of exit
status cannot say which, because the declared failures have already spent it.

**Is this unit's own criterion still outstanding?** Clearing its manifest entry
in the same commit is the mechanical signal that a unit is done, so a unit whose
suite went green while its own criterion is still listed did not give that
signal, and the next unit inherits a criterion the manifest calls unimplemented.
Judged per criterion rather than per project: a project mid-build always has
other units' entries outstanding, and reading those would fail every unit until
the last one.

**A manifest that exists and will not parse forgives nothing.** It is a
committed file an agent authored, so a broken one is a defect its author has to
fix, and reading it as "nothing was pending" would hand the verdict back to an
exit status whose declaration nobody can check: an agent whose pending test
started passing could corrupt the manifest to buy the pass back. It does not
block a green run as well, because a file that fails every task in the project
is a worse failure than the one it guards. A workspace with NO manifest is a
different fact entirely: nothing was ever declared, and the run's status stands.
"""

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Final

from synthorg.core.criterion_match import criterion_key
from synthorg.engine.errors import EnvironmentConfigError
from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME
from synthorg.engine.workspace.environment.manifest import (
    EnvironmentManifest,
    read_manifest,
)
from synthorg.engine.workspace.environment.pending import classify_pending
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import ENVIRONMENT_PENDING_MANIFEST_UNREAD
from synthorg.persistence.code_execution_protocol import CodeExecutionPurpose

logger = get_logger(__name__)

#: Sentence-ending punctuation dropped before comparing two criteria. Only the
#: trailing run: punctuation inside a criterion is part of what it says.
_TRAILING_PUNCTUATION: Final[str] = ".!?;:, "


def _load(
    workspace_root: Path | None, project_id: str
) -> tuple[Path, EnvironmentManifest] | None:
    """Read the project's manifest, with the workspace it was read from.

    The workspace travels with it because the report path the manifest declares
    is resolved against that same directory, and re-deriving it at the call site
    is a second answer to where the project lives.

    Returns:
        The workspace and its manifest, or ``None`` when no workspace was
        wired, no manifest is committed, or the committed one will not parse.
    """
    if workspace_root is None:
        return None
    workspace = project_workspace_dir(workspace_root, project_id)
    if not (workspace / DEFAULT_MANIFEST_FILENAME).is_file():
        return None
    try:
        return workspace, read_manifest(workspace, filename=DEFAULT_MANIFEST_FILENAME)
    except EnvironmentConfigError as exc:
        logger.warning(
            ENVIRONMENT_PENDING_MANIFEST_UNREAD,
            project_id=project_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


def _match_key(criterion: str) -> str:
    """Reduce a criterion to what the two sides of this match can agree on.

    ``criterion_key`` is the shared normaliser and is applied first, but it
    keeps trailing punctuation, and the two texts here have different authors:
    one is the objective's own wording, the other is what an agent typed into
    the manifest after reading it in a brief. A full stop it dropped would make
    every comparison miss, and a check that never matches passes every unit
    silently, which is the worst way for a gate to fail.

    Returns:
        The comparison key.
    """
    return criterion_key(criterion).rstrip(_TRAILING_PUNCTUATION)


def failure_was_declared(
    *,
    workspace_root: Path | None,
    project_id: str,
) -> bool:
    """Whether a failing test run is exactly the failure the project declared.

    Args:
        workspace_root: Base directory projects live under, or ``None`` when
            the oracle was wired without one, which forgives nothing rather
            than guessing at a workspace.
        project_id: The project whose workspace holds the manifest.

    Returns:
        ``True`` only when a readable manifest declares pending tests and the
        run's report shows every one of them failing its own assertion with
        nothing else broken.
    """
    loaded = _load(workspace_root, project_id)
    if loaded is None:
        return False
    workspace, manifest = loaded
    if not manifest.pending:
        return False
    return classify_pending(
        manifest.pending,
        workspace_path=workspace,
        test_report_path=manifest.test_report_path,
    ).green


def unclaimed_criteria(
    criteria: Iterable[str],
    *,
    workspace_root: Path | None,
    project_id: str,
) -> tuple[str, ...]:
    """Which of a task's own criteria the manifest still calls unimplemented.

    Args:
        criteria: The task's acceptance criteria, in its own spelling.
        workspace_root: Base directory projects live under, or ``None``.
        project_id: The project whose workspace holds the manifest.

    Returns:
        The criteria still listed pending, in the task's order. Empty when the
        unit cleared its entries, when the project declares none, or when there
        is no readable manifest to ask.
    """
    loaded = _load(workspace_root, project_id)
    if loaded is None:
        return ()
    _workspace, manifest = loaded
    outstanding = {_match_key(str(entry.criterion)) for entry in manifest.pending}
    return tuple(
        criterion for criterion in criteria if _match_key(criterion) in outstanding
    )


def declared_gates(
    *,
    workspace_root: Path | None,
    project_id: str,
) -> Mapping[CodeExecutionPurpose, str]:
    """The gate commands the project declares, by what each one proves.

    The oracle asks for a passing run of each, so a gate the manifest declares
    and the run never exercised is a unit that is not finished. Derived from
    the manifest rather than listed anywhere, which is what stops a field being
    added that nothing ever requires evidence of.

    Returns:
        One entry per declared gate; empty when there is no readable manifest,
        which is the same "nothing to require" as a project declaring none.
    """
    loaded = _load(workspace_root, project_id)
    if loaded is None:
        return {}
    _workspace, manifest = loaded
    return manifest.declared_gates


__all__ = ["declared_gates", "failure_was_declared", "unclaimed_criteria"]
