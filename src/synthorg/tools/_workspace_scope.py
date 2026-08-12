"""The project a sandboxed command runs inside.

File tools resolve their root per call from the bound execution identity, so
every write lands in ``<base>/projects/<project_id>``. A sandbox given no
project mounts ``<base>`` instead, which puts the command one directory above
everything the agent just wrote: ``write_file`` reports success, the file
exists, and the shell that runs it cannot see it. Both halves therefore read
the same identity, and the answer is resolved per call rather than fixed at
construction because the tool registry is built once per boot and shared by
every agent and every project.
"""

from synthorg.core.execution_identity import current_execution_identity
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import WORKSPACE_SCOPE_UNRESOLVED

logger = get_logger(__name__)


def current_project_id() -> NotBlankStr | None:
    """Resolve the project the running command belongs to.

    An unresolved scope is reported, and says which of the two it is. Both
    land the command on the shared workspace root, where every other project's
    files are, so neither is a quiet default: an unbound identity is a wiring
    fault on the calling path, while a run that genuinely has no project is a
    surface reaching the sandbox that nobody scoped.

    Returns:
        The bound execution identity's project id, or ``None`` when the tool
        is exercised outside a run or the run has no project to scope to.
    """
    identity = current_execution_identity()
    if identity is None:
        logger.warning(
            WORKSPACE_SCOPE_UNRESOLVED,
            reason="no execution identity bound",
        )
        return None
    if identity.project_id is None:
        logger.warning(
            WORKSPACE_SCOPE_UNRESOLVED,
            reason="run declares no project",
            execution_id=identity.execution_id,
            task_id=identity.task_id,
        )
    return identity.project_id
