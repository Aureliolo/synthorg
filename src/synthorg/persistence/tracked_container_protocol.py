"""Tracked Docker container persistence protocol and record.

The Docker sandbox lifecycle (``src/synthorg/tools/sandbox/``) tracks
the set of running sandbox containers in a plain ``dict[str, str |
None]`` (sandbox container id to optional sidecar container id).
Pre-WP-1 this dict was lost on restart, so a process crash mid-task
would leave orphan containers running on the Docker daemon with no
record of who owned them.

This repository persists one row per tracked sandbox. On restart the
sandbox lifecycle calls :func:`reconcile_tracked_containers` (in
``src/synthorg/tools/sandbox/reconciliation.py``) which:

1. Loads every row via :meth:`load_all`.
2. Queries the Docker daemon for containers carrying the
   ``synthorg.managed=true`` label.
3. Drops DB rows whose container is no longer in Docker (died outside
   our control).
4. Stops + removes Docker containers that are not in DB (orphans from
   a previous unclean shutdown).
5. Keeps rows for containers present in both sources.
"""

from datetime import datetime  # noqa: TC003 -- runtime needed by Pydantic field
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001


class TrackedContainerRecord(BaseModel):
    """Persisted tracking row for one sandbox container.

    Attributes:
        container_id: Docker container id of the sandbox.
        sidecar_id: Optional Docker container id of the paired sidecar,
            or ``None`` when the sandbox has no sidecar.
        created_at: UTC wall-clock timestamp of container creation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    container_id: NotBlankStr = Field(description="Docker container id of the sandbox")
    sidecar_id: NotBlankStr | None = Field(
        default=None, description="Docker container id of the paired sidecar"
    )
    created_at: datetime = Field(description="UTC timestamp of container creation")


@runtime_checkable
class TrackedContainerRepository(Protocol):
    """Persistence interface for tracked Docker sandbox containers."""

    async def save(self, record: TrackedContainerRecord) -> None:
        """Insert or replace the tracking row for one container.

        Args:
            record: Tracking record to persist.

        Raises:
            QueryError: If the underlying write fails.
        """
        ...

    async def get(self, container_id: NotBlankStr) -> TrackedContainerRecord | None:
        """Read the tracking row for one container, or ``None`` if absent.

        Args:
            container_id: Docker container id to look up.

        Returns:
            The persisted record, or ``None`` if no row exists.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    async def delete(self, container_id: NotBlankStr) -> bool:
        """Delete the tracking row for one container.

        Args:
            container_id: Docker container id to remove.

        Returns:
            ``True`` if a row was deleted, ``False`` if no row existed.

        Raises:
            QueryError: If the underlying DELETE fails.
        """
        ...

    async def load_all(self) -> tuple[TrackedContainerRecord, ...]:
        """Load every tracking row (called once at sandbox-subsystem start).

        Returns:
            All persisted records, order unspecified.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...
