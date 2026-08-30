"""Unit tests for ``synthorg.tools.sandbox.reconciliation``.

Two groups. The first is the reconciliation itself: DB-only rows are
dropped, Docker-only orphans are stopped and removed, and containers in
both sources are kept.

The second is the guards that decide whether a container is a candidate at
all, and they matter more than they look: this pass stops and removes what
it believes nobody owns, so each guard is the difference between reclaiming
debris and killing an agent mid-task. They are tested separately because
they are independent, and each covers a case the others miss.

The test stubs implement ``DockerClientProtocol`` directly so the suite
does not touch a real Docker daemon. Repository is stubbed via an
in-memory dict.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.background_job_protocol import (
    BackgroundJobRecord,
    BackgroundJobStatus,
)
from synthorg.persistence.tracked_container_protocol import (
    TrackedContainerRecord,
)
from synthorg.tools.sandbox.reconciliation import (
    DockerClientProtocol,
    ManagedContainer,
    ReconciliationOutcome,
    reap_orphaned_background_jobs,
    reconcile_tracked_containers,
)
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit

_OURS = "deadbeefdeadbeef"
_THEIRS = "0123456789abcdef"
_BOOT = 1000.0
_BEFORE_BOOT = 100.0
_AFTER_BOOT = 2000.0

_WORKSPACE_ROOT = Path("/synthorg-test/ours/workspaces")
_OUR_MOUNT = str(_WORKSPACE_ROOT / "agent-1" / "project")
_THEIR_MOUNT = "/synthorg-test/theirs/workspaces/agent-1/project"


class _StubRepo:
    """Minimal stand-in for ``TrackedContainerRepository``."""

    def __init__(self, records: Iterable[TrackedContainerRecord]) -> None:
        self._rows: dict[str, TrackedContainerRecord] = {
            r.container_id: r for r in records
        }
        self.deleted: list[str] = []

    async def load_all(self) -> tuple[TrackedContainerRecord, ...]:
        return tuple(self._rows.values())

    async def delete(self, entity_id: str) -> bool:
        self.deleted.append(entity_id)
        return self._rows.pop(entity_id, None) is not None

    async def save(self, entity: TrackedContainerRecord) -> None:  # pragma: no cover
        self._rows[entity.container_id] = entity

    async def get(
        self, entity_id: str
    ) -> TrackedContainerRecord | None:  # pragma: no cover
        return self._rows.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[TrackedContainerRecord, ...]:  # pragma: no cover
        items = sorted(self._rows.values(), key=lambda r: r.container_id)
        return tuple(items[offset : offset + limit])


class _StubDockerClient:
    """Minimal stand-in for ``DockerClientProtocol``."""

    def __init__(self, managed: Iterable[ManagedContainer]) -> None:
        self._managed = list(managed)
        self.stopped: list[str] = []
        self.removed: list[str] = []

    async def list_managed_containers(self) -> tuple[ManagedContainer, ...]:
        return tuple(self._managed)

    async def stop_container(self, container_id: str) -> None:
        self.stopped.append(container_id)

    async def remove_container(self, container_id: str) -> None:
        self.removed.append(container_id)


def _make_record(container_id: str) -> TrackedContainerRecord:
    return TrackedContainerRecord(
        container_id=container_id,
        sidecar_id=None,
        created_at=datetime.now(UTC),
    )


def _managed(
    container_id: str,
    *,
    deployment_id: str | None = _OURS,
    created_at: float = _BEFORE_BOOT,
    workspace_source: str | None = None,
) -> ManagedContainer:
    """Build a daemon-side container that is ours and predates boot by default."""
    return ManagedContainer(
        container_id=container_id,
        deployment_id=deployment_id,
        created_at=created_at,
        workspace_source=workspace_source,
    )


async def _reconcile(
    repo: _StubRepo, docker: _StubDockerClient
) -> ReconciliationOutcome:
    """Run a pass with this deployment's identity, boot time and workspace.

    Returns:
        The reconciliation outcome.
    """
    return await reconcile_tracked_containers(
        repo=repo,
        docker=docker,
        deployment_id=_OURS,
        started_at=_BOOT,
        workspace_root=_WORKSPACE_ROOT,
    )


async def test_reconciliation_keeps_both_present_containers() -> None:
    """Containers in both DB and Docker are reported as kept and untouched."""
    repo = _StubRepo([_make_record("c1"), _make_record("c2")])
    docker = _StubDockerClient([_managed("c1"), _managed("c2")])

    outcome = await _reconcile(repo, docker)

    assert outcome.kept == ("c1", "c2")
    assert outcome.db_only_dropped == ()
    assert outcome.docker_only_killed == ()
    assert repo.deleted == []
    assert docker.stopped == []
    assert docker.removed == []


async def test_reconciliation_drops_db_only_rows() -> None:
    """A container in DB but not in Docker has its DB row removed."""
    repo = _StubRepo([_make_record("c1"), _make_record("c2")])
    docker = _StubDockerClient([_managed("c1")])  # c2 is gone

    outcome = await _reconcile(repo, docker)

    assert outcome.kept == ("c1",)
    assert outcome.db_only_dropped == ("c2",)
    assert outcome.docker_only_killed == ()
    assert repo.deleted == ["c2"]
    # No Docker calls for db-only rows -- the container is already gone.
    assert docker.stopped == []
    assert docker.removed == []


async def test_reconciliation_kills_docker_only_orphans() -> None:
    """A container in Docker but not in DB is stopped + removed as an orphan."""
    repo = _StubRepo([_make_record("c1")])
    docker = _StubDockerClient([_managed("c1"), _managed("orphan")])

    outcome = await _reconcile(repo, docker)

    assert outcome.kept == ("c1",)
    assert outcome.db_only_dropped == ()
    assert outcome.docker_only_killed == ("orphan",)
    assert repo.deleted == []
    assert docker.stopped == ["orphan"]
    assert docker.removed == ["orphan"]


async def test_reconciliation_handles_full_mismatch() -> None:
    """Disjoint DB and Docker sets both clean up."""
    repo = _StubRepo([_make_record("db-only")])
    docker = _StubDockerClient([_managed("docker-only")])

    outcome = await _reconcile(repo, docker)

    assert outcome.kept == ()
    assert outcome.db_only_dropped == ("db-only",)
    assert outcome.docker_only_killed == ("docker-only",)
    assert repo.deleted == ["db-only"]
    assert docker.stopped == ["docker-only"]
    assert docker.removed == ["docker-only"]


async def test_another_deployments_container_is_never_touched() -> None:
    """A container labelled for a different deployment is left alone.

    Two backends can share one Docker daemon, and the other one's container
    is absent from this database for the ordinary reason that it is not
    ours. Without this guard that reads as an orphan, and the pass would
    stop a container another live backend is using.
    """
    repo = _StubRepo([])
    docker = _StubDockerClient([_managed("theirs", deployment_id=_THEIRS)])

    outcome = await _reconcile(repo, docker)

    assert outcome.docker_only_killed == ()
    assert outcome.foreign_skipped == ("theirs",)
    assert docker.stopped == []
    assert docker.removed == []


async def test_container_created_after_boot_is_never_touched() -> None:
    """A container newer than this process is not a candidate.

    The pass is safe because it runs before this process has created a
    sandbox, and this guard is what makes that hold without depending on
    activation order: anything created after we started is somebody's live
    work, whether ours or a peer's on the same database.
    """
    repo = _StubRepo([])
    docker = _StubDockerClient([_managed("fresh", created_at=_AFTER_BOOT)])

    outcome = await _reconcile(repo, docker)

    assert outcome.docker_only_killed == ()
    assert docker.stopped == []
    assert docker.removed == []


async def test_one_of_ours_created_after_boot_is_not_reported_foreign() -> None:
    """Too new to reclaim is not the same fact as belonging to somebody else.

    Both outcomes leave the container alone, so the distinction only shows
    up in what the boot log says happened. Reporting live work of ours as
    another deployment's is how an operator reads a shared daemon into a
    picture that has never existed.
    """
    repo = _StubRepo([])
    docker = _StubDockerClient([_managed("fresh", created_at=_AFTER_BOOT)])

    outcome = await _reconcile(repo, docker)

    assert outcome.foreign_skipped == ()


async def test_unlabelled_container_from_another_install_is_spared() -> None:
    """An unlabelled container mounting a tree that is not ours is left alone.

    This is the case the deployment label cannot answer. During an upgrade a
    second installation on the same daemon still runs containers created by
    a build that predates the label, and they are live. The only evidence
    either carries is the workspace it was handed, and a deployment hands
    out its own tree and no other.
    """
    repo = _StubRepo([])
    docker = _StubDockerClient(
        [_managed("theirs", deployment_id=None, workspace_source=_THEIR_MOUNT)]
    )

    outcome = await _reconcile(repo, docker)

    assert outcome.docker_only_killed == ()
    assert outcome.foreign_skipped == ("theirs",)
    assert docker.stopped == []
    assert docker.removed == []


async def test_unlabelled_container_with_no_mount_is_spared() -> None:
    """No label and no workspace mount proves nothing, so nothing is done.

    "Probably ours" and "somebody else's live work" are the same picture
    from here, and only one of the two readings is recoverable.
    """
    repo = _StubRepo([])
    docker = _StubDockerClient([_managed("unprovable", deployment_id=None)])

    outcome = await _reconcile(repo, docker)

    assert outcome.docker_only_killed == ()
    assert outcome.foreign_skipped == ("unprovable",)
    assert docker.stopped == []
    assert docker.removed == []


async def test_unlabelled_legacy_container_in_our_workspace_is_reclaimed() -> None:
    """A container predating the label, mounting our tree, is ours to reclaim.

    It can only have come from an older build of this deployment, and the
    whole point of the pass is to reclaim exactly that debris; skipping it
    would strand every container created before the label shipped.
    """
    repo = _StubRepo([])
    docker = _StubDockerClient(
        [_managed("legacy", deployment_id=None, workspace_source=_OUR_MOUNT)]
    )

    outcome = await _reconcile(repo, docker)

    assert outcome.docker_only_killed == ("legacy",)
    assert outcome.foreign_skipped == ()
    assert docker.stopped == ["legacy"]
    assert docker.removed == ["legacy"]


async def test_two_unlabelled_deployments_are_separated_by_their_mounts() -> None:
    """Ownership is per container, never inferred from the rest of the daemon.

    Both installations are mid-upgrade, so neither container carries a
    label and no third container is present to hint that the daemon is
    shared. A rule that reasons about the set rather than the container
    either reclaims both or spares both; each is wrong for one of them.
    """
    repo = _StubRepo([])
    docker = _StubDockerClient(
        [
            _managed("legacy-ours", deployment_id=None, workspace_source=_OUR_MOUNT),
            _managed(
                "legacy-theirs", deployment_id=None, workspace_source=_THEIR_MOUNT
            ),
        ]
    )

    outcome = await _reconcile(repo, docker)

    assert outcome.docker_only_killed == ("legacy-ours",)
    assert outcome.foreign_skipped == ("legacy-theirs",)
    assert docker.removed == ["legacy-ours"]


class _FailingDeleteRepo(_StubRepo):
    """Repository whose delete raises for one nominated row."""

    def __init__(
        self, records: Iterable[TrackedContainerRecord], failing_id: str
    ) -> None:
        super().__init__(records)
        self._failing_id = failing_id
        self.attempted: list[str] = []

    @override
    async def delete(self, entity_id: str) -> bool:
        self.attempted.append(entity_id)
        if entity_id == self._failing_id:
            msg = "row is locked"
            raise RuntimeError(msg)
        return await super().delete(entity_id)


class _FailingStopDockerClient(_StubDockerClient):
    """Docker client whose stop always raises."""

    @override
    async def stop_container(self, container_id: str) -> None:
        msg = "container is not running"
        raise RuntimeError(msg)


async def test_one_undeletable_row_does_not_strand_the_rest() -> None:
    """A row the archive refuses is logged and the pass carries on.

    Reconciliation is a whole-daemon sweep run once per boot. Aborting on
    the first refusal would leave every later row and every later orphan
    for the next restart, which is how a single wedged row turns into
    unbounded debris.
    """
    repo = _FailingDeleteRepo([_make_record("wedged"), _make_record("fine")], "wedged")
    docker = _StubDockerClient([])

    outcome = await _reconcile(repo, docker)

    assert outcome.db_only_dropped == ("fine", "wedged")
    assert repo.attempted == ["fine", "wedged"]


async def test_a_container_that_will_not_stop_is_still_removed() -> None:
    """Removal follows a failed stop rather than being skipped by it.

    The daemon is asked to force the removal, so a container that refused
    to stop still goes; treating the stop failure as fatal would leave the
    container, its volume and the image it pins exactly where they were.
    """
    repo = _StubRepo([])
    docker = _FailingStopDockerClient([_managed("orphan")])

    outcome = await _reconcile(repo, docker)

    assert outcome.docker_only_killed == ("orphan",)
    assert docker.removed == ["orphan"]


def test_docker_client_protocol_runtime_checkable() -> None:
    """The DockerClientProtocol is runtime-checkable for spec-based stubs."""
    stub = _StubDockerClient([])
    assert isinstance(stub, DockerClientProtocol)


class _StubBackgroundJobRepo:
    """Minimal stand-in for ``BackgroundJobRepository``."""

    def __init__(self, records: Iterable[BackgroundJobRecord]) -> None:
        self._rows: dict[str, BackgroundJobRecord] = {r.job_id: r for r in records}

    async def load_all(self) -> tuple[BackgroundJobRecord, ...]:
        return tuple(self._rows.values())

    async def save(self, entity: BackgroundJobRecord) -> None:
        self._rows[entity.job_id] = entity

    async def save_if_live(
        self, entity: BackgroundJobRecord
    ) -> bool:  # pragma: no cover
        current = self._rows.get(entity.job_id)
        if current is None or current.status not in {
            BackgroundJobStatus.PENDING,
            BackgroundJobStatus.RUNNING,
        }:
            return False
        self._rows[entity.job_id] = entity
        return True

    async def get(self, entity_id: str) -> BackgroundJobRecord | None:
        return self._rows.get(entity_id)  # pragma: no cover

    async def delete(self, entity_id: str) -> bool:  # pragma: no cover
        return self._rows.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[BackgroundJobRecord, ...]:  # pragma: no cover
        items = sorted(self._rows.values(), key=lambda r: r.job_id)
        return tuple(items[offset : offset + limit])

    async def list_by_container(
        self,
        container_id: str,
        *,
        statuses: frozenset[BackgroundJobStatus] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[BackgroundJobRecord, ...]:
        matches = [
            r
            for r in self._rows.values()
            if r.container_id == container_id
            and (statuses is None or r.status in statuses)
        ]
        return tuple(matches[offset : offset + limit])

    async def count_live_by_owner(self, owner_id: str) -> int:  # pragma: no cover
        return 0

    async def list_by_owner(
        self, owner_id: str, *, limit: int = 100, offset: int = 0
    ) -> tuple[BackgroundJobRecord, ...]:  # pragma: no cover
        return ()


def _make_job_record(
    *, job_id: str, container_id: str, status: BackgroundJobStatus
) -> BackgroundJobRecord:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return BackgroundJobRecord(
        job_id=NotBlankStr(job_id),
        container_id=NotBlankStr(container_id),
        owner_id=NotBlankStr("agent-1:rw"),
        command_repr="sleep 300",
        pid=42,
        status=status,
        output_path="/tmp/.synthorg-jobs/x/output",  # noqa: S108
        started_at=now,
        updated_at=now,
        max_duration_seconds=3600.0,
    )


class TestReapOrphanedBackgroundJobs:
    """The DB-only sweep run immediately after container reconciliation."""

    async def test_live_job_on_a_dropped_container_is_orphaned(self) -> None:
        repo = _StubBackgroundJobRepo(
            [
                _make_job_record(
                    job_id="j1",
                    container_id="gone",
                    status=BackgroundJobStatus.RUNNING,
                )
            ]
        )
        reaped = await reap_orphaned_background_jobs(
            repo=repo, kept_container_ids=frozenset(), clock=FakeClock()
        )
        assert reaped == ("gone",)
        record = await repo.get("j1")
        assert record is not None
        assert record.status == BackgroundJobStatus.ORPHANED

    async def test_live_job_on_a_kept_container_is_untouched(self) -> None:
        repo = _StubBackgroundJobRepo(
            [
                _make_job_record(
                    job_id="j1",
                    container_id="kept",
                    status=BackgroundJobStatus.RUNNING,
                )
            ]
        )
        reaped = await reap_orphaned_background_jobs(
            repo=repo, kept_container_ids=frozenset({"kept"}), clock=FakeClock()
        )
        assert reaped == ()
        record = await repo.get("j1")
        assert record is not None
        assert record.status == BackgroundJobStatus.RUNNING

    async def test_already_terminal_job_on_a_dropped_container_is_untouched(
        self,
    ) -> None:
        """A finished job's container being gone is not an orphan condition.

        Its own terminal status already answers "what happened"; reaping
        would overwrite that with a less informative one.
        """
        repo = _StubBackgroundJobRepo(
            [
                _make_job_record(
                    job_id="j1",
                    container_id="gone",
                    status=BackgroundJobStatus.COMPLETED,
                )
            ]
        )
        reaped = await reap_orphaned_background_jobs(
            repo=repo, kept_container_ids=frozenset(), clock=FakeClock()
        )
        assert reaped == ()
        record = await repo.get("j1")
        assert record is not None
        assert record.status == BackgroundJobStatus.COMPLETED

    async def test_no_rows_reaps_nothing(self) -> None:
        repo = _StubBackgroundJobRepo([])
        reaped = await reap_orphaned_background_jobs(
            repo=repo, kept_container_ids=frozenset(), clock=FakeClock()
        )
        assert reaped == ()
