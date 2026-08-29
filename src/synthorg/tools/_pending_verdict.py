# module-kind: code
"""What a test run's exit status means once a project declares pending tests.

A skeleton commits one test per acceptance criterion before anything implements
the contract, so a correct skeleton's suite exits NON-ZERO. Recording that as a
failed run would block the very deliverable the stage exists to produce, and
the build/test oracle is a pure function of ``CodeExecutionRecord.passed``, so
the correction has to land on the record rather than beside it. Two answers to
"did this run pass" is the shape this codebase refuses.

The correction is narrow in both directions, and the manifest is the only thing
that can widen it. A run whose pending tests all failed their own declared
assertions, with nothing else broken, passed. A run with any other pending
outcome, or with an ordinary test broken alongside them, did not. The exit
status alone can express neither, because it is a single bit that the pending
failures have already spent.

**A manifest that exists and does not parse is red.** It is a committed file an
agent authored, so a broken one is a defect its author has to fix, and reading
it as "nothing is pending" would hand back the exit status of a suite whose
declaration nobody can check. A workspace with NO manifest is a different fact:
nothing was ever declared pending, so the exit status is the honest answer and
is used unchanged.
"""

from pathlib import Path

from synthorg.engine.errors import EnvironmentConfigError
from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME
from synthorg.engine.workspace.environment.manifest import read_manifest
from synthorg.engine.workspace.environment.pending import classify_pending
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import ENVIRONMENT_PENDING_MANIFEST_UNREAD

logger = get_logger(__name__)


def resolve_passed(
    *,
    exited_zero: bool,
    workspace_root: Path | None,
    project_id: str,
) -> bool:
    """Decide what a finished test run should record as ``passed``.

    Args:
        exited_zero: Whether the run's own exit status was success.
        workspace_root: Base directory projects live under, or ``None`` when
            the caller was wired without one, which leaves the exit status
            untouched rather than guessing at a workspace.
        project_id: The project whose workspace holds the manifest.

    Returns:
        The verdict to persist: the exit status when nothing is declared
        pending, and the pending report's own verdict when something is.
    """
    if workspace_root is None:
        return exited_zero
    workspace = project_workspace_dir(workspace_root, project_id)
    if not (workspace / DEFAULT_MANIFEST_FILENAME).is_file():
        return exited_zero
    try:
        manifest = read_manifest(workspace, filename=DEFAULT_MANIFEST_FILENAME)
    except EnvironmentConfigError as exc:
        logger.warning(
            ENVIRONMENT_PENDING_MANIFEST_UNREAD,
            project_id=project_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False
    if not manifest.pending:
        return exited_zero
    return classify_pending(
        manifest.pending,
        workspace_path=workspace,
        test_report_path=manifest.test_report_path,
    ).green


__all__ = ["resolve_passed"]
