# module-kind: code
"""Project-scoped namespace derivation for memory reads and writes.

One place decides how a project maps onto storage namespaces, so the
write side (which namespace a captured memory lands in) and the read side
(which namespaces a recall or a self-editing tool may see) cannot drift
apart and reopen the cross-project bleed they exist to close.

A project-scoped write lands in ``project:<id>`` alone, so one project's
memory never becomes visible to another. A project-scoped read spans the
agent's shared ``default`` namespace unioned with ``project:<id>``, so an
agent working inside a project recalls both its own cross-project memory
and that project's, and never a third project's.
"""

from typing import Final

from synthorg.core.execution_identity import current_execution_identity
from synthorg.core.types import NotBlankStr

#: Namespace unscoped agent memory lands in (mirrors
#: ``MemoryStoreRequest.namespace``'s default). Project-scoped recall
#: unions this with the project namespace so an agent keeps its own
#: cross-project memories.
DEFAULT_MEMORY_NAMESPACE: Final[str] = "default"

#: Prefix that scopes a memory to one project. Project-scoped writes land
#: in ``{prefix}<id>`` and project-scoped recall filters on it, so one
#: project's memory never bleeds into another's.
PROJECT_NAMESPACE_PREFIX: Final[str] = "project:"


def _clean(project_id: str | None) -> str | None:
    """Return a non-blank project id, or ``None``.

    Returns:
        The stripped id when it carries a value, else ``None``.
    """
    if project_id is None:
        return None
    stripped = project_id.strip()
    return stripped or None


def write_namespace(project_id: str | None) -> NotBlankStr:
    """Return the namespace a memory captured under *project_id* must land in.

    Returns:
        The project's own namespace when scoped, else the shared default.
    """
    project = _clean(project_id)
    if project is None:
        return NotBlankStr(DEFAULT_MEMORY_NAMESPACE)
    return NotBlankStr(f"{PROJECT_NAMESPACE_PREFIX}{project}")


def read_namespaces(project_id: str | None) -> frozenset[NotBlankStr] | None:
    """Return the namespaces a recall under *project_id* may see.

    Returns:
        ``None`` (all namespaces) for unscoped work; otherwise the shared
        default unioned with the project's own namespace.
    """
    project = _clean(project_id)
    if project is None:
        return None
    return frozenset(
        {
            NotBlankStr(DEFAULT_MEMORY_NAMESPACE),
            NotBlankStr(f"{PROJECT_NAMESPACE_PREFIX}{project}"),
        }
    )


def ambient_write_namespace() -> NotBlankStr:
    """Return the write namespace for the currently-executing run.

    Reads the ambient :class:`ExecutionIdentity` the engine binds around
    a run, so a capture hook needs no project argument threaded to it.

    Returns:
        The project's namespace when the run is project-scoped, else the
        shared default.
    """
    identity = current_execution_identity()
    return write_namespace(identity.project_id if identity is not None else None)


def ambient_read_namespaces() -> frozenset[NotBlankStr] | None:
    """Return the read scope for the currently-executing run.

    Returns:
        The project-scoped namespace union when the run is project-scoped,
        else ``None`` (all namespaces).
    """
    identity = current_execution_identity()
    return read_namespaces(identity.project_id if identity is not None else None)
