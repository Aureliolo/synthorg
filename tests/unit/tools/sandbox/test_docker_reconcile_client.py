"""Unit tests for the daemon-payload boundary of the reconcile client.

The reconciliation pass stops and removes containers, and every input to
that decision arrives in one ``GET /containers/json`` object. So the tests
here are about what the boundary refuses: a payload missing the field the
age guard depends on must be rejected outright rather than defaulted, and a
container with no workspace mount must read as having none rather than as
having some other deployment's.

The second group covers the daemon calls themselves against a stand-in
client, because two of the three carry a flag whose absence turns a
reclaimed container into a quieter leak.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from synthorg.core.boundary import parse_typed
from synthorg.tools.sandbox._mount_paths import CONTAINER_WORKSPACE
from synthorg.tools.sandbox.deployment_identity import (
    DEPLOYMENT_LABEL,
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
)
from synthorg.tools.sandbox.docker_reconcile_client import (
    AiodockerReconcileClient,
    _DaemonContainer,
)
from tests._shared import FakeDockerClient

pytestmark = pytest.mark.unit

_HOST_WORKSPACE = str(Path("/srv/synthorg/workspaces/agent-1"))


def _payload(**overrides: object) -> dict[str, object]:
    """Build a daemon container object with the fields the pass reads.

    Args:
        **overrides: Keys to replace or, with a ``None`` value, drop.

    Returns:
        The payload as the daemon would return it.
    """
    payload: dict[str, object] = {
        "Id": "c1",
        "Created": 1700000000.0,
        "Labels": {DEPLOYMENT_LABEL: "deadbeefdeadbeef"},
        "Mounts": [
            {"Destination": "/etc/resolv.conf", "Source": "/host/resolv.conf"},
            {"Destination": CONTAINER_WORKSPACE, "Source": _HOST_WORKSPACE},
        ],
    }
    for key, value in overrides.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload


def _parse(payload: dict[str, object]) -> _DaemonContainer:
    """Parse through the same boundary the production client uses.

    Returns:
        The validated container.
    """
    return parse_typed("docker.containers.list", payload, _DaemonContainer)


def test_a_full_payload_yields_every_field_the_pass_reads() -> None:
    """Id, creation time, deployment label and workspace mount all survive."""
    parsed = _parse(_payload())

    assert parsed.container_id == "c1"
    assert parsed.created == 1700000000.0
    assert (parsed.labels or {})[DEPLOYMENT_LABEL] == "deadbeefdeadbeef"
    assert parsed.workspace_source() == _HOST_WORKSPACE


def test_a_missing_creation_time_is_refused() -> None:
    """No default, because absent would read as older than every boot.

    The age guard spares anything created at or after this process started.
    A container with no creation time defaulted to zero sits before every
    possible boot, so the whole daemon becomes eligible for the orphan
    sweep on a payload the pass merely failed to understand.
    """
    with pytest.raises(ValidationError):
        _parse(_payload(Created=None))


def test_a_non_numeric_creation_time_is_refused() -> None:
    """A field of the wrong shape is refused for the same reason as absent."""
    with pytest.raises(ValidationError):
        _parse(_payload(Created="yesterday"))


def test_a_missing_id_is_refused() -> None:
    """Nothing can be stopped or removed without the id naming it."""
    with pytest.raises(ValidationError):
        _parse(_payload(Id=None))


def test_absent_labels_read_as_no_deployment_claim() -> None:
    """A container may legitimately carry no labels at all."""
    parsed = _parse(_payload(Labels=None))

    assert parsed.labels is None


def test_a_container_without_a_workspace_mount_has_no_source() -> None:
    """No workspace mount is reported as none, never as somebody else's."""
    parsed = _parse(
        _payload(Mounts=[{"Destination": "/etc/hosts", "Source": "/host/hosts"}])
    )

    assert parsed.workspace_source() is None


def test_an_empty_workspace_source_reads_as_none() -> None:
    """A tmpfs workspace names no host path, so it proves no ownership."""
    parsed = _parse(
        _payload(Mounts=[{"Destination": CONTAINER_WORKSPACE, "Source": ""}])
    )

    assert parsed.workspace_source() is None


def test_unknown_daemon_fields_are_ignored() -> None:
    """A container object carries dozens of fields this pass never reads.

    Forbidding them would reject every genuine response the moment Docker
    adds one, which on this path means declining the sweep forever.
    """
    parsed = _parse(_payload(State="running", NetworkSettings={"Networks": {}}))

    assert parsed.container_id == "c1"


class _FakeContainerHandle:
    """One container, recording what was asked of it."""

    def __init__(self, container_id: str, calls: list[tuple[object, ...]]) -> None:
        self._id = container_id
        self._calls = calls

    async def stop(self) -> None:
        self._calls.append(("stop", self._id))

    async def delete(self, *, force: bool = False, v: bool = False) -> None:
        self._calls.append(("delete", self._id, force, v))


class _FakeListEntry:
    """An ``aiodocker`` container object, which exposes its raw mapping."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._container = payload


class _FakeContainers:
    """The ``containers`` sub-client the reconcile surface talks to."""

    def __init__(
        self, payloads: list[dict[str, object]], calls: list[tuple[object, ...]]
    ) -> None:
        self._payloads = payloads
        self._calls = calls
        self.list_kwargs: dict[str, object] = {}

    async def list(self, **kwargs: object) -> list[_FakeListEntry]:
        self.list_kwargs = kwargs
        return [_FakeListEntry(payload) for payload in self._payloads]

    def container(self, container_id: str) -> _FakeContainerHandle:
        return _FakeContainerHandle(container_id, self._calls)


def _client(
    payloads: list[dict[str, object]],
) -> tuple[AiodockerReconcileClient, _FakeContainers, list[tuple[object, ...]]]:
    """Build the reconcile client over a session-free daemon double.

    Returns:
        The client, the containers surface, and the shared call log.
    """
    calls: list[tuple[object, ...]] = []
    containers = _FakeContainers(payloads, calls)
    return AiodockerReconcileClient(FakeDockerClient(containers)), containers, calls


async def test_the_listing_asks_the_daemon_to_apply_the_managed_filter() -> None:
    """The label filter is the boundary, so it goes to the daemon, not here.

    A shared Docker host carries other people's containers, and filtering
    in Python would mean this process had already read them into a
    candidate set. Nothing downstream can act on a container the daemon
    never returned.
    """
    client, containers, _ = _client([_payload()])

    await client.list_managed_containers()

    assert containers.list_kwargs["all"] is True
    filters = json.loads(str(containers.list_kwargs["filters"]))
    assert filters == {"label": [f"{MANAGED_LABEL}={MANAGED_LABEL_VALUE}"]}


async def test_the_listing_carries_every_field_the_verdict_needs() -> None:
    """Id, deployment label, creation time and workspace mount all arrive."""
    client, _, _ = _client([_payload()])

    (managed,) = await client.list_managed_containers()

    assert managed.container_id == "c1"
    assert managed.deployment_id == "deadbeefdeadbeef"
    assert managed.created_at == 1700000000.0
    assert managed.workspace_source == _HOST_WORKSPACE


async def test_an_unlabelled_container_arrives_with_no_deployment_id() -> None:
    """Absent labels are reported as absent, never as a match."""
    client, _, _ = _client([_payload(Labels=None)])

    (managed,) = await client.list_managed_containers()

    assert managed.deployment_id is None


async def test_removal_takes_the_anonymous_volume_with_it() -> None:
    """Each sandbox leaves a volume behind, and it is the point of the sweep.

    Removing the container without ``v`` converts one leak into a quieter
    one that no container listing will ever show. ``force`` because a
    container that failed to stop still has to go.
    """
    client, _, calls = _client([])

    await client.stop_container("c1")
    await client.remove_container("c1")

    assert calls == [("stop", "c1"), ("delete", "c1", True, True)]
