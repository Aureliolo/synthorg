"""Unit tests for ``synthorg.tools.sandbox.reconciliation``.

Three behaviour buckets:

* DB-only rows are dropped from persistence.
* Docker-only orphans (with the ``synthorg.managed`` label) are
  stopped and removed via the daemon.
* Containers present in both sources are kept and reported via
  ``ReconciliationOutcome.kept``.

The test stubs implement ``DockerClientProtocol`` directly so the
suite does not touch a real Docker daemon. Repository is stubbed via
an in-memory dict.
"""

from collections.abc import Iterable
from datetime import UTC, datetime

import pytest

from synthorg.persistence.tracked_container_protocol import (
    TrackedContainerRecord,
)
from synthorg.tools.sandbox.reconciliation import (
    DockerClientProtocol,
    reconcile_tracked_containers,
)

pytestmark = pytest.mark.unit


class _StubRepo:
    """Minimal stand-in for ``TrackedContainerRepository``."""

    def __init__(self, records: Iterable[TrackedContainerRecord]) -> None:
        self._rows: dict[str, TrackedContainerRecord] = {
            r.container_id: r for r in records
        }
        self.deleted: list[str] = []

    async def load_all(self) -> tuple[TrackedContainerRecord, ...]:
        return tuple(self._rows.values())

    async def delete(self, container_id: str) -> bool:
        self.deleted.append(container_id)
        return self._rows.pop(container_id, None) is not None

    async def save(self, record: TrackedContainerRecord) -> None:  # pragma: no cover
        self._rows[record.container_id] = record

    async def get(
        self, container_id: str
    ) -> TrackedContainerRecord | None:  # pragma: no cover
        return self._rows.get(container_id)


class _StubDockerClient:
    """Minimal stand-in for ``DockerClientProtocol``."""

    def __init__(self, managed: Iterable[str]) -> None:
        self._managed = list(managed)
        self.stopped: list[str] = []
        self.removed: list[str] = []

    async def list_managed_containers(self) -> tuple[str, ...]:
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


async def test_reconciliation_keeps_both_present_containers() -> None:
    """Containers in both DB and Docker are reported as kept and untouched."""
    repo = _StubRepo([_make_record("c1"), _make_record("c2")])
    docker = _StubDockerClient(["c1", "c2"])

    outcome = await reconcile_tracked_containers(repo=repo, docker=docker)

    assert outcome.kept == ("c1", "c2")
    assert outcome.db_only_dropped == ()
    assert outcome.docker_only_killed == ()
    assert repo.deleted == []
    assert docker.stopped == []
    assert docker.removed == []


async def test_reconciliation_drops_db_only_rows() -> None:
    """A container in DB but not in Docker has its DB row removed."""
    repo = _StubRepo([_make_record("c1"), _make_record("c2")])
    docker = _StubDockerClient(["c1"])  # c2 is gone

    outcome = await reconcile_tracked_containers(repo=repo, docker=docker)

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
    docker = _StubDockerClient(["c1", "orphan"])  # orphan never landed in DB

    outcome = await reconcile_tracked_containers(repo=repo, docker=docker)

    assert outcome.kept == ("c1",)
    assert outcome.db_only_dropped == ()
    assert outcome.docker_only_killed == ("orphan",)
    assert repo.deleted == []
    assert docker.stopped == ["orphan"]
    assert docker.removed == ["orphan"]


async def test_reconciliation_handles_full_mismatch() -> None:
    """Disjoint DB and Docker sets both clean up."""
    repo = _StubRepo([_make_record("db-only")])
    docker = _StubDockerClient(["docker-only"])

    outcome = await reconcile_tracked_containers(repo=repo, docker=docker)

    assert outcome.kept == ()
    assert outcome.db_only_dropped == ("db-only",)
    assert outcome.docker_only_killed == ("docker-only",)
    assert repo.deleted == ["db-only"]
    assert docker.stopped == ["docker-only"]
    assert docker.removed == ["docker-only"]


def test_docker_client_protocol_runtime_checkable() -> None:
    """The DockerClientProtocol is runtime-checkable for spec-based stubs."""
    stub = _StubDockerClient([])
    assert isinstance(stub, DockerClientProtocol)
