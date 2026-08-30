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
from pathlib import Path
from typing import Protocol, runtime_checkable

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docker import (
    DOCKER_CONTAINER_REMOVED,
)
from synthorg.persistence.background_job_protocol import (
    LIVE_BACKGROUND_JOB_STATUSES,
    BackgroundJobRepository,
)
from synthorg.persistence.tracked_container_protocol import (
    TrackedContainerRepository,
)
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry
from synthorg.tools.sandbox.deployment_identity import path_is_within

logger = get_logger(__name__)


@dataclass(frozen=True)
class ManagedContainer:
    """A container on the daemon carrying the ``synthorg.managed`` label.

    Attributes:
        container_id: The Docker container id.
        deployment_id: Value of the ``synthorg.deployment`` label, or
            ``None`` for a container created before that label shipped.
        created_at: Daemon-reported creation time, epoch seconds.
        workspace_source: Host path mounted as the container's workspace,
            or ``None`` when it has no such mount. This is what proves
            ownership of a container that predates the deployment label:
            the mount names the tree it was given, and a deployment only
            ever hands out its own.
    """

    container_id: str
    deployment_id: str | None
    created_at: float
    workspace_source: str | None = None


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
        foreign_skipped: Container IDs this deployment could not prove it
            owns, whether labelled for another deployment or carrying no
            evidence at all; left untouched.
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


def _is_ours(
    container: ManagedContainer,
    *,
    deployment_id: str,
    workspace_root: Path,
) -> bool:
    """Report whether *container* belongs to this deployment.

    Ownership is proved, never assumed. The label settles it outright when
    present. Without one, the only evidence a legacy container carries is
    the tree it was handed: a deployment mounts its own workspace and
    nothing else, so a mount inside ours could not have come from another
    installation. A container offering neither is left alone, because
    "probably ours" and "somebody else's live work" are the same picture.

    Args:
        container: The daemon-side container under test.
        deployment_id: This deployment's identity.
        workspace_root: The workspace this deployment hands to sandboxes.

    Returns:
        ``True`` when the container is ours to reclaim.
    """
    if container.deployment_id is not None:
        return container.deployment_id == deployment_id
    if container.workspace_source is None:
        return False
    return path_is_within(container.workspace_source, workspace_root)


async def _drop_stale_rows(
    repo: TrackedContainerRepository, container_ids: Sequence[str]
) -> None:
    """Delete rows whose container the daemon no longer has."""
    for container_id in container_ids:
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


async def _remove_orphans(
    docker: DockerClientProtocol, container_ids: Sequence[str]
) -> None:
    """Stop and remove containers this deployment owns but no longer tracks."""
    for container_id in container_ids:
        for phase, action in (
            ("reconcile_orphan_stop", docker.stop_container),
            ("reconcile_orphan_remove", docker.remove_container),
        ):
            try:
                await action(container_id)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    DOCKER_CONTAINER_REMOVED,
                    phase=phase,
                    container_id=container_id[:12],
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )


async def reconcile_tracked_containers(
    *,
    repo: TrackedContainerRepository,
    docker: DockerClientProtocol,
    deployment_id: str,
    started_at: float,
    workspace_root: Path,
) -> ReconciliationOutcome:
    """Reconcile DB-tracked containers with the Docker daemon state.

    Called once at sandbox-subsystem start, before this process can have
    created a sandbox of its own. That timing is what makes the orphan
    verdict safe: a container carrying this deployment's label at boot
    belongs to a predecessor that is gone, so removing it cannot take
    down live work. The returned outcome lets the caller hydrate its
    in-memory tracking dict from the ``kept`` set.

    Ownership is proved per container by :func:`_is_ours`, never inferred
    from what else happens to be on the daemon: a label that names this
    deployment, or, for a container predating the label, a workspace mount
    inside this deployment's own tree. Anything else is another
    installation's business and is left alone.

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
        workspace_root: The workspace this deployment hands to sandboxes,
            which is what identifies an unlabelled container as ours.

    Returns:
        :class:`ReconciliationOutcome` summarising the reconciliation
        pass.
    """
    db_records = await repo.load_all()
    db_ids = {r.container_id for r in db_records}
    managed = await docker.list_managed_containers()

    all_ids = {c.container_id for c in managed}
    # Ownership and age are separate questions and answered separately: one
    # of ours created after boot is live work, not somebody else's container,
    # and folding the two would report it as foreign.
    ours: set[str] = set()
    reclaimable: set[str] = set()
    for container in managed:
        if not _is_ours(
            container, deployment_id=deployment_id, workspace_root=workspace_root
        ):
            continue
        ours.add(container.container_id)
        if container.created_at < started_at:
            reclaimable.add(container.container_id)
    foreign = sorted(all_ids - ours)

    kept = sorted(db_ids & all_ids)
    db_only = sorted(db_ids - all_ids)
    docker_only = sorted(reclaimable - db_ids)

    await _drop_stale_rows(repo, db_only)
    await _remove_orphans(docker, docker_only)

    return ReconciliationOutcome(
        kept=tuple(kept),
        db_only_dropped=tuple(db_only),
        docker_only_killed=tuple(docker_only),
        foreign_skipped=tuple(foreign),
    )


async def reap_orphaned_background_jobs(
    *,
    repo: BackgroundJobRepository,
    kept_container_ids: frozenset[str],
    clock: Clock | None = None,
) -> tuple[str, ...]:
    """Mark every live background job whose container did not survive reconciliation.

    A DB-only sweep, run immediately after :func:`reconcile_tracked_containers`
    with its own ``kept`` set: the container-side cleanup (stop/remove) already
    happened there, since a background job carries no Docker-level label of its
    own and is invisible to that pass. This closes the loop on the job-record
    side -- a job whose container is gone before it reached a terminal status
    on its own has no process left to poll, cancel, or read output from.

    Args:
        repo: Background-job repository.
        kept_container_ids: Container ids :func:`reconcile_tracked_containers`
            kept (present in both DB and daemon). Any live job whose
            ``container_id`` is not in this set is orphaned.
        clock: Clock seam for the reaped rows' ``updated_at`` stamp;
            defaults to ``SystemClock`` via :class:`BackgroundJobRegistry`.

    Returns:
        The distinct container ids whose jobs were reaped, sorted.
    """
    registry = BackgroundJobRegistry(repo, clock=clock)
    rows = await repo.load_all()
    orphaned_containers = {
        r.container_id
        for r in rows
        if r.status in LIVE_BACKGROUND_JOB_STATUSES
        and r.container_id not in kept_container_ids
    }
    for container_id in sorted(orphaned_containers):
        await registry.reap_for_container(
            NotBlankStr(container_id), reason="boot_reconciliation"
        )
    return tuple(sorted(orphaned_containers))
