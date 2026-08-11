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


def current_project_id() -> NotBlankStr | None:
    """Resolve the project the running command belongs to.

    Returns:
        The bound execution identity's project id, or ``None`` when the tool
        is exercised outside a run or the run has no project to scope to.
    """
    identity = current_execution_identity()
    return identity.project_id if identity is not None else None
