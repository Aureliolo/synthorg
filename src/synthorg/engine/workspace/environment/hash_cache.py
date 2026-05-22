"""In-process provisioned-environment memo.

The durable provisioning cache is the persisted
:class:`~synthorg.core.project_environment.ProjectEnvironment` row.  This
in-process memo is a fast-path on top of it: once a project's environment
is provisioned, a repeated request whose live declaration hash matches the
memoised row's hash short-circuits without a persistence round-trip or a
re-provision.  Any hash change (declaration edited) misses the memo and
falls through to the full provision path.

The :class:`~synthorg.engine.workspace.environment.service.EnvironmentService`
holds a per-project lock around every memo read/write, so the plain dict
needs no internal locking.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synthorg.core.project_environment import ProjectEnvironment


class ProvisionedEnvironmentCache:
    """Maps ``project_id`` to its last provisioned environment row."""

    __slots__ = ("_rows",)

    def __init__(self) -> None:
        self._rows: dict[str, ProjectEnvironment] = {}

    def get(self, project_id: str) -> ProjectEnvironment | None:
        """Return the memoised environment for *project_id*, if any."""
        return self._rows.get(project_id)

    def set(self, project_id: str, environment: ProjectEnvironment) -> None:
        """Record the provisioned *environment* for *project_id*."""
        self._rows[project_id] = environment

    def invalidate(self, project_id: str) -> None:
        """Drop any memoised environment for *project_id*."""
        self._rows.pop(project_id, None)


__all__ = ["ProvisionedEnvironmentCache"]
