"""Docker sandbox container reconciliation on lifecycle start.

:class:`TrackedContainerRepository` persists the sandbox lifecycle's
``_tracked_containers`` dict so it survives a restart; this module is
the reconciliation pass the sandbox subsystem runs at start to close
the loop with the actual Docker daemon state. Without it, a process
crash mid-task would leave orphan containers running on the daemon
with no record of who owned them.

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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docker import (
    DOCKER_CONTAINER_REMOVED,
)
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
class ManagedContainer:
    """A container on the daemon carrying the ``synthorg.managed`` label.

    Attributes:
        container_id: The Docker container id.
        deployment_id: Value of the ``synthorg.deployment`` label, or
            ``None`` for a container created before that label shipped.
        created_at: Daemon-reported creation time, epoch seconds.
    """

    container_id: str
    deployment_id: str | None
    created_at: float


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
        foreign_skipped: Container IDs carrying another deployment's
            ``synthorg.deployment`` label; left untouched.
    """

    kept: tuple[str, ...]
    db_only_dropped: tuple[str, ...]
    docker_only_killed: tuple[str, ...]
    foreign_skipped: tuple[str, ...]


@runtime_checkable
class DockerClientProtocol(Protocol):
    """Minimum Docker client surface used by reconciliation.

    A subset of ``aiodocker.Docker`` so tests can substitute a
    duck-typed stub. The real client implements all three methods
    natively.
    """

    async def list_managed_containers(self) -> Sequence[ManagedContainer]:
        """List containers carrying the ``synthorg.managed`` label.

        Returns:
            Result of type ``Sequence[ManagedContainer]``.
        """
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
    deployment_id: str,
    started_at: float,
) -> ReconciliationOutcome:
    """Reconcile DB-tracked containers with the Docker daemon state.

    Called once at sandbox-subsystem start, before this process can have
    created a sandbox of its own. That timing is what makes the orphan
    verdict safe: a container carrying this deployment's label at boot
    belongs to a predecessor that is gone, so removing it cannot take
    down live work. The returned outcome lets the caller hydrate its
    in-memory tracking dict from the ``kept`` set.

    Containers labelled for a *different* deployment are never touched,
    because another backend on the same daemon may be using them right
    now. A container with no deployment label predates the label and is
    treated as ours, since it can only have come from an older build of
    this code and leaving it would mean the debris is never reclaimed;
    that claim is dropped as soon as any other deployment's label is
    visible on the daemon, because then an unlabelled container might be
    its predecessor instead.

    One window remains open, and it is worth stating rather than implying
    it away: a peer process sharing this deployment identity creates a
    container before it persists the row naming it. A pass that reads the
    daemon inside that gap sees a container with no row and no way to tell
    it from an orphan. The gap is milliseconds and needs this boot to land
    inside it, but it is not nothing.

    Args:
        repo: TrackedContainerRepository (DB persistence).
        docker: Docker client (production: aiodocker wrapper; tests:
            mock_of[DockerClientProtocol]).
        deployment_id: This deployment's identity, from
            :func:`synthorg.tools.sandbox.deployment_identity.deployment_id_for`.
        started_at: Epoch seconds this process started. Containers created
            at or after it are never candidates, so no ordering assumption
            about when this pass runs relative to the rest of boot can turn
            a live container into an orphan.

    Returns:
        :class:`ReconciliationOutcome` summarising the reconciliation
        pass.
    """
    db_records = await repo.load_all()
    db_ids = {r.container_id for r in db_records}
    managed = await docker.list_managed_containers()

    all_ids = {c.container_id for c in managed}
    # An unlabelled container predates the label, so it can only have come
    # from an older build of this code: ours, and reclaimable. That holds
    # only while this daemon serves one deployment. The moment another
    # deployment's label is visible, an unlabelled container might equally
    # be ITS predecessor, and it is still upgrading, so the claim is
    # withdrawn rather than guessed. The cost of being wrong is asymmetric:
    # skipping leaves debris, reclaiming takes down somebody's live work.
    legacy_is_ours = not {
        c.deployment_id
        for c in managed
        if c.deployment_id is not None and c.deployment_id != deployment_id
    }
    ours = {
        c.container_id
        for c in managed
        if (
            c.deployment_id == deployment_id
            or (c.deployment_id is None and legacy_is_ours)
        )
        and c.created_at < started_at
    }
    foreign = sorted(all_ids - ours)

    kept = sorted(db_ids & all_ids)
    db_only = sorted(db_ids - all_ids)
    docker_only = sorted(ours - db_ids)

    # DB-only: drop stale rows. The container is gone; there is
    # nothing to clean up on the daemon side.
    for container_id in db_only:
        try:
            await repo.delete(container_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                DOCKER_CONTAINER_REMOVED,
                phase="reconcile_orphan_stop",
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        try:
            await docker.remove_container(container_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
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
        foreign_skipped=tuple(foreign),
    )
