"""Unit tests for ``synthorg.tools.sandbox.reconciliation``.

Two groups. The first is the reconciliation itself: DB-only rows are
dropped, Docker-only orphans are stopped and removed, and containers in
both sources are kept.

The second is the three guards that decide whether a container is a
candidate at all, and they matter more than they look: this pass stops and
removes what it believes nobody owns, so each guard is the difference
between reclaiming debris and killing an agent mid-task. They are tested
separately because they are independent, and each covers a case the others
miss.

The test stubs implement ``DockerClientProtocol`` directly so the suite
does not touch a real Docker daemon. Repository is stubbed via an
in-memory dict.
"""

from collections.abc import Iterable
from datetime import UTC, datetime

import pytest

from synthorg.persistence.tracked_container_protocol import (
    TrackedContainerRecord,
)
from synthorg.tools.sandbox.reconciliation import (
    DockerClientProtocol,
    ManagedContainer,
    ReconciliationOutcome,
    reconcile_tracked_containers,
)

pytestmark = pytest.mark.unit

_OURS = "deadbeefdeadbeef"
_THEIRS = "0123456789abcdef"
_BOOT = 1000.0
_BEFORE_BOOT = 100.0
_AFTER_BOOT = 2000.0


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
) -> ManagedContainer:
    """Build a daemon-side container that is ours and predates boot by default."""
    return ManagedContainer(
        container_id=container_id,
        deployment_id=deployment_id,
        created_at=created_at,
    )


async def _reconcile(
    repo: _StubRepo, docker: _StubDockerClient
) -> ReconciliationOutcome:
    """Run a pass with this deployment's identity and boot time.

    Returns:
        The reconciliation outcome.
    """
    return await reconcile_tracked_containers(
        repo=repo,
        docker=docker,
        deployment_id=_OURS,
        started_at=_BOOT,
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


async def test_unlabelled_container_is_spared_while_another_deployment_is_visible() -> (
    None
):
    """The legacy claim is withdrawn on a daemon serving more than one of us.

    An unlabelled container is only safely ours while nothing else here has
    a label: during an upgrade, another deployment that has not yet
    restarted onto this build has unlabelled containers too, and they are
    live. Skipping leaves debris; reclaiming takes down their work.
    """
    repo = _StubRepo([])
    docker = _StubDockerClient(
        [
            _managed("legacy", deployment_id=None),
            _managed("theirs", deployment_id=_THEIRS),
        ]
    )

    outcome = await _reconcile(repo, docker)

    assert outcome.docker_only_killed == ()
    assert outcome.foreign_skipped == ("legacy", "theirs")
    assert docker.stopped == []
    assert docker.removed == []


async def test_unlabelled_legacy_container_is_reclaimed() -> None:
    """A container predating the deployment label is treated as ours.

    It can only have come from an older build of this code, and the whole
    point of the pass is to reclaim exactly that debris; skipping it would
    leave every container created before the label shipped stranded for
    good.
    """
    repo = _StubRepo([])
    docker = _StubDockerClient([_managed("legacy", deployment_id=None)])

    outcome = await _reconcile(repo, docker)

    assert outcome.docker_only_killed == ("legacy",)
    assert outcome.foreign_skipped == ()
    assert docker.stopped == ["legacy"]
    assert docker.removed == ["legacy"]


def test_docker_client_protocol_runtime_checkable() -> None:
    """The DockerClientProtocol is runtime-checkable for spec-based stubs."""
    stub = _StubDockerClient([])
    assert isinstance(stub, DockerClientProtocol)
