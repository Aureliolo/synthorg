"""Docker sandbox container reconciliation on lifecycle start.

Pre-WP-1 the sandbox lifecycle's ``_tracked_containers`` dict was lost
on process restart, so a process crash mid-task would leave orphan
containers running on the Docker daemon with no record of who owned
them. WP-1 added :class:`TrackedContainerRepository` so the dict
survives restarts; this module is the reconciliation pass that the
sandbox subsystem runs at start to close the loop with the actual
Docker daemon state.

Algorithm:

1. Load every tracked-container row from the DB.
2. Query the Docker daemon for containers carrying the
   ``synthorg.managed=true`` label.
3. Reconcile:
   - **DB-only**: container ID exists in DB but the daemon has no such
     container. The container died outside our control between
     shutdown and start; drop the stale DB row.
   - **Docker-only**: container exists on the daemon with our label
     but DB has no record. Orphan from a previous unclean shutdown
     where the DB write was lost or rolled back; stop + remove the
     container.
   - **Both**: keep -- the container is still alive and we still own
     it. Hydrate the in-memory tracking dict.

The reconciliation function is a pure async function over a small
``DockerClientProtocol`` + the repository so it can be unit-tested
without a real Docker daemon.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docker import (
    DOCKER_CONTAINER_REMOVED,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.persistence.tracked_container_protocol import (
        TrackedContainerRepository,
    )

logger = get_logger(__name__)


MANAGED_LABEL: str = "synthorg.managed"
"""Docker label set on every sandbox container created by SynthOrg.

The reconciliation pass filters by this label so it can identify
orphan containers (label present, no DB row) without misidentifying
unrelated containers on a shared daemon.
"""

MANAGED_LABEL_VALUE: str = "true"
"""Value carried in the ``synthorg.managed`` label.

Stored as a literal string because Docker labels are strings; the
Python bool is convenient at the call site but does not round-trip.
"""


@dataclass(frozen=True)
class ReconciliationOutcome:
    """Summary of one reconciliation pass.

    Attributes:
        kept: Container IDs present in both the DB and Docker.
        db_only_dropped: Container IDs that existed in DB but not on
            the Docker daemon; their DB rows were deleted.
        docker_only_killed: Container IDs that existed on the Docker
            daemon (with the ``synthorg.managed`` label) but not in
            the DB; they were stopped + removed as orphans.
    """

    kept: tuple[str, ...]
    db_only_dropped: tuple[str, ...]
    docker_only_killed: tuple[str, ...]


@runtime_checkable
class DockerClientProtocol(Protocol):
    """Minimum Docker client surface used by reconciliation.

    A subset of ``aiodocker.Docker`` so tests can substitute a
    duck-typed stub. The real client implements all three methods
    natively.
    """

    async def list_managed_containers(self) -> Sequence[str]:
        """List container IDs carrying the ``synthorg.managed`` label."""
        ...

    async def stop_container(self, container_id: str) -> None:
        """Best-effort stop of a container by id."""
        ...

    async def remove_container(self, container_id: str) -> None:
        """Best-effort remove of a container by id."""
        ...


async def reconcile_tracked_containers(
    *,
    repo: TrackedContainerRepository,
    docker: DockerClientProtocol,
) -> ReconciliationOutcome:
    """Reconcile DB-tracked containers with the Docker daemon state.

    Called once at sandbox-subsystem start. The returned outcome lets
    the caller hydrate its in-memory tracking dict from the ``kept``
    set; ``db_only_dropped`` and ``docker_only_killed`` are surfaced
    primarily for telemetry and tests.

    Args:
        repo: TrackedContainerRepository (DB persistence).
        docker: Docker client (production: aiodocker wrapper; tests:
            mock_of[DockerClientProtocol]).

    Returns:
        :class:`ReconciliationOutcome` summarising the reconciliation
        pass.
    """
    db_records = await repo.load_all()
    db_ids = {r.container_id for r in db_records}
    docker_ids = set(await docker.list_managed_containers())

    kept = sorted(db_ids & docker_ids)
    db_only = sorted(db_ids - docker_ids)
    docker_only = sorted(docker_ids - db_ids)

    # DB-only: drop stale rows. The container is gone; there is
    # nothing to clean up on the daemon side.
    for container_id in db_only:
        try:
            await repo.delete(container_id)
        except Exception as exc:
            logger.warning(
                DOCKER_CONTAINER_REMOVED,
                phase="reconcile_db_only_drop",
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    # Docker-only: orphan containers. Stop + remove via the daemon;
    # the DB has no record to clean up.
    for container_id in docker_only:
        try:
            await docker.stop_container(container_id)
        except Exception as exc:
            logger.warning(
                DOCKER_CONTAINER_REMOVED,
                phase="reconcile_orphan_stop",
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        try:
            await docker.remove_container(container_id)
        except Exception as exc:
            logger.warning(
                DOCKER_CONTAINER_REMOVED,
                phase="reconcile_orphan_remove",
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    return ReconciliationOutcome(
        kept=tuple(kept),
        db_only_dropped=tuple(db_only),
        docker_only_killed=tuple(docker_only),
    )
