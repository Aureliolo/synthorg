"""Tests for the container-hosted MCP stdio transport.

The transport exists because the shipped stack could not launch a stdio MCP
server at all: the hardened backend image has no node to spawn one as a child
and no ``docker`` CLI to spawn one in a container, so both branches raised
``FileNotFoundError`` on every boot. So the cases below cover what a live
launch depends on and what no integration test would notice going wrong: the
isolation the create config asks for, the labels that let an orphan be
reclaimed, a JSON-RPC round trip over the attach stream, and a teardown that
destroys the container whatever else happened.
"""

import json
from contextlib import AbstractAsyncContextManager
from typing import Final, cast, override

import aiodocker
import anyio
import pytest
import structlog
from aiodocker.containers import DockerContainer
from aiodocker.stream import Message, Stream
from mcp import types
from mcp.client._transport import TransportStreams
from mcp.shared.message import SessionMessage

from synthorg.observability.events.mcp import MCP_SANDBOX_RESERVED_ENV_DROPPED
from synthorg.tools.mcp.container_stdio import container_stdio_client
from synthorg.tools.mcp.errors import MCPConnectionError
from synthorg.tools.mcp.sandbox import MCPSandboxConfig
from synthorg.tools.sandbox.deployment_identity import (
    DEPLOYMENT_LABEL,
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
)
from tests._shared import FakeDockerClient

pytestmark = pytest.mark.unit

_STDOUT: Final[int] = 1
_STDERR: Final[int] = 2

#: A fictitious package and credential env var: the transport is
#: vendor-agnostic infrastructure and names no real provider.
_EXAMPLE_PACKAGE: Final[str] = "@example-org/example-mcp-server"
_EXAMPLE_ENV_VAR: Final[str] = "EXAMPLE_API_KEY"
_SERVER: Final[str] = "example-mcp"


class _FakeStream(Stream):
    """A scripted attach stream recording everything written to stdin.

    Subclasses the real :class:`Stream` (skipping its network setup) so the
    typeguard-instrumented transport accepts it at the typed boundary.
    """

    def __init__(self, frames: list[tuple[int, bytes]] | None = None) -> None:
        self._frames: list[tuple[int, bytes]] = list(frames or [])
        self.written: list[bytes] = []
        self.closed = False
        self._eof = anyio.Event()
        # ``Stream.__del__`` reads it, and skipping ``super().__init__`` (which
        # would open a session) leaves it unset.
        self._resp = None

    @override
    async def read_out(self) -> Message | None:
        if self._frames:
            stream_id, data = self._frames.pop(0)
            return Message(stream=stream_id, data=data)
        # Nothing scripted: block until the transport closes the stream, which
        # is what a live server does between requests.
        await self._eof.wait()
        return None

    @override
    async def write_in(self, data: bytes) -> None:
        self.written.append(data)

    @override
    async def close(self) -> None:
        self.closed = True
        self._eof.set()


class _FakeContainer(DockerContainer):
    """The ``aiodocker`` container surface the transport uses.

    Subclasses the real container (skipping its client binding) because the
    transport is typeguard-instrumented and hands one back at a typed
    boundary.
    """

    def __init__(
        self,
        stream: _FakeStream,
        *,
        start_error: Exception | None = None,
    ) -> None:
        self._id = "c" * 64
        self._stream = stream
        self._start_error = start_error
        self.started = False
        self.stopped = False
        self.deleted = False

    @override
    def attach(self, **_kwargs: object) -> _FakeStream:
        """Return the scripted stream.

        Returns:
            The stream the transport pumps.
        """
        return self._stream

    @override
    async def start(self, **_kwargs: object) -> None:
        """Start, or fail the way the daemon would.

        Raises:
            Exception: Whatever the test scripted.
        """
        if self._start_error is not None:
            raise self._start_error
        self.started = True

    @override
    async def stop(self, **_kwargs: object) -> None:
        """Record the stop."""
        self.stopped = True

    @override
    async def delete(self, **_kwargs: object) -> None:
        """Record the delete."""
        self.deleted = True


class _FakeContainers:
    """Records the create body and hands back one fake container."""

    def __init__(
        self,
        container: _FakeContainer | None,
        *,
        create_error: Exception | None = None,
    ) -> None:
        self._container = container
        self._create_error = create_error
        self.created_config: dict[str, object] | None = None

    async def create(
        self, config: dict[str, object], **_kwargs: object
    ) -> _FakeContainer:
        """Record the create body and return the container.

        Returns:
            The scripted container.

        Raises:
            Exception: Whatever the test scripted.
        """
        self.created_config = config
        if self._create_error is not None:
            raise self._create_error
        assert self._container is not None
        return self._container

    def container(self, _container_id: str) -> _FakeContainer:
        """Look a container up by id.

        Returns:
            The scripted container.
        """
        assert self._container is not None
        return self._container


class _FakeDocker(FakeDockerClient):
    """A daemon-free client whose ``close`` the transport may call."""

    def __init__(self, containers: _FakeContainers) -> None:
        super().__init__(containers)
        self.closed = False

    @override
    async def close(self) -> None:
        """Record the close."""
        self.closed = True


class _Harness:
    """One transport session's collaborators, with the daemon replaced."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        frames: list[tuple[int, bytes]] | None = None,
        start_error: Exception | None = None,
        create_error: Exception | None = None,
    ) -> None:
        self.stream = _FakeStream(frames)
        self.container = (
            None
            if create_error is not None
            else _FakeContainer(self.stream, start_error=start_error)
        )
        self.containers = _FakeContainers(self.container, create_error=create_error)
        self.docker = _FakeDocker(self.containers)
        # The transport constructs its own client, which is the seam a live
        # launch has: patched on the module it constructs through.
        monkeypatch.setattr(aiodocker, "Docker", self._client)

    def _client(self) -> _FakeDocker:
        """Stand in for ``aiodocker.Docker()``.

        Returns:
            The daemon-free client.
        """
        return self.docker

    def open(
        self,
        *,
        sandbox: MCPSandboxConfig | None = None,
        env: dict[str, str] | None = None,
    ) -> AbstractAsyncContextManager[TransportStreams]:
        """Open a transport session over the fake daemon.

        Returns:
            The transport's async context manager.
        """
        return container_stdio_client(
            command="npx",
            args=["-y", _EXAMPLE_PACKAGE],
            env=env or {},
            sandbox=sandbox or MCPSandboxConfig(),
            server_name=_SERVER,
        )

    @property
    def config(self) -> dict[str, object]:
        """The create body the daemon was given.

        Returns:
            The recorded config.
        """
        assert self.containers.created_config is not None
        return self.containers.created_config

    @property
    def host(self) -> dict[str, object]:
        """The isolation policy the daemon was given.

        Returns:
            The create body's ``HostConfig``.
        """
        return cast("dict[str, object]", self.config["HostConfig"])

    @property
    def env(self) -> list[str]:
        """The container environment the daemon was given.

        Returns:
            The ``KEY=value`` lines.
        """
        return cast("list[str]", self.config["Env"])

    @property
    def cmd(self) -> list[str]:
        """The launch the daemon was given.

        Returns:
            The argv.
        """
        return cast("list[str]", self.config["Cmd"])

    @property
    def labels(self) -> dict[str, str]:
        """The labels the daemon recorded.

        Returns:
            The label mapping.
        """
        return cast("dict[str, str]", self.config["Labels"])


async def _fail_mid_session(harness: _Harness) -> None:
    """Raise while the session is open, so the teardown path is the one tested.

    Raises:
        RuntimeError: Always, standing in for whatever a consumer does wrong.
    """
    async with harness.open():
        msg = "boom"
        raise RuntimeError(msg)


def _message_id(message: SessionMessage | Exception) -> object:
    """Read the JSON-RPC id off a delivered message.

    Returns:
        The id the server answered with.
    """
    assert isinstance(message, SessionMessage)
    return message.message.model_dump(by_alias=True, exclude_none=True)["id"]


class TestTheContainerIsolationIsAskedFor:
    async def test_create_config_carries_the_hardening(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch)
        async with harness.open():
            pass
        assert harness.host["CapDrop"] == ["ALL"]
        assert harness.host["SecurityOpt"] == ["no-new-privileges"]
        assert harness.host["ReadonlyRootfs"] is True
        assert harness.host["AutoRemove"] is False
        assert harness.host["Tmpfs"]

    async def test_stdin_is_attached_before_the_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A server whose stdin is closed cannot receive the first request."""
        harness = _Harness(monkeypatch)
        async with harness.open():
            pass
        assert harness.config["OpenStdin"] is True
        assert harness.config["AttachStdin"] is True
        assert harness.config["StdinOnce"] is False
        assert harness.config["Tty"] is False

    async def test_the_resource_policy_is_converted_to_daemon_units(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch)
        async with harness.open(
            sandbox=MCPSandboxConfig(memory_limit="256m", cpus="0.5", pids_limit=64)
        ):
            pass
        assert harness.host["Memory"] == 256 * 1024 * 1024
        assert harness.host["NanoCpus"] == 500_000_000
        assert harness.host["PidsLimit"] == 64


class TestCredentialsAndTrustedControls:
    async def test_a_credential_reaches_the_container_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch)
        async with harness.open(env={_EXAMPLE_ENV_VAR: "super-secret-value"}):
            pass
        assert f"{_EXAMPLE_ENV_VAR}=super-secret-value" in harness.env
        # Never in the argv, where every process on the host could read it.
        assert all("super-secret-value" not in arg for arg in harness.cmd)

    async def test_a_supplied_control_cannot_override_the_trusted_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-enabling install scripts is the npm RCE vector, not a preference."""
        harness = _Harness(monkeypatch)
        with structlog.testing.capture_logs() as cap:
            async with harness.open(
                env={"NPM_CONFIG_IGNORE_SCRIPTS": "false", "HOME": "/evil"}
            ):
                pass
        assert "NPM_CONFIG_IGNORE_SCRIPTS=true" in harness.env
        assert "NPM_CONFIG_IGNORE_SCRIPTS=false" not in harness.env
        assert "HOME=/evil" not in harness.env
        dropped = [e for e in cap if e.get("event") == MCP_SANDBOX_RESERVED_ENV_DROPPED]
        assert len(dropped) == 2
        assert all(e.get("log_level") == "warning" for e in dropped)


class TestTheContainerCanBeReclaimed:
    async def test_labels_name_the_deployment_that_created_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hard kill leaves the server running with nothing attached to it.

        The reconciliation pass filters on the managed label and proves
        ownership from the deployment label; a container carrying neither is
        left alone for ever.
        """
        harness = _Harness(monkeypatch)
        async with harness.open(
            sandbox=MCPSandboxConfig(deployment_id="deadbeefdeadbeef")
        ):
            pass
        assert harness.labels[MANAGED_LABEL] == MANAGED_LABEL_VALUE
        assert harness.labels[DEPLOYMENT_LABEL] == "deadbeefdeadbeef"
        assert harness.labels["synthorg.mcp.server"] == _SERVER

    async def test_an_unattributed_container_claims_no_deployment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch)
        async with harness.open():
            pass
        assert DEPLOYMENT_LABEL not in harness.labels


class TestTheSessionTalksToTheServer:
    async def test_a_request_is_written_as_one_json_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch)
        request = types.JSONRPCRequest(jsonrpc="2.0", id=1, method="ping")
        async with harness.open() as (_read, write):
            await write.send(SessionMessage(request))
        assert harness.stream.written
        payload = harness.stream.written[0]
        assert payload.endswith(b"\n")
        assert json.loads(payload)["method"] == "ping"

    async def test_a_response_arrives_as_a_session_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(
            monkeypatch,
            frames=[(_STDOUT, b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n')],
        )
        async with harness.open() as (read, _write):
            message = await read.receive()
        assert _message_id(message) == 1

    async def test_a_response_split_across_frames_is_reassembled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(
            monkeypatch,
            frames=[
                (_STDOUT, b'{"jsonrpc":"2.0","id":7,'),
                (_STDOUT, b'"result":{}}\n'),
            ],
        )
        async with harness.open() as (read, _write):
            message = await read.receive()
        assert _message_id(message) == 7

    async def test_a_malformed_line_travels_as_a_value_not_a_teardown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One bad line must not kill a live transport, matching the SDK."""
        harness = _Harness(
            monkeypatch,
            frames=[
                (_STDOUT, b"not json at all\n"),
                (_STDOUT, b'{"jsonrpc":"2.0","id":2,"result":{}}\n'),
            ],
        )
        async with harness.open() as (read, _write):
            first = await read.receive()
            second = await read.receive()
        assert isinstance(first, Exception)
        assert _message_id(second) == 2

    async def test_stderr_is_never_parsed_as_a_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(
            monkeypatch,
            frames=[
                (_STDERR, b"npm warn: something\n"),
                (_STDOUT, b'{"jsonrpc":"2.0","id":3,"result":{}}\n'),
            ],
        )
        async with harness.open() as (read, _write):
            message = await read.receive()
        assert _message_id(message) == 3


class TestTheContainerIsAlwaysDestroyed:
    async def test_a_clean_session_stops_and_removes_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch)
        async with harness.open():
            pass
        assert harness.container is not None
        assert harness.container.stopped
        assert harness.container.deleted
        assert harness.stream.closed
        assert harness.docker.closed

    async def test_a_failure_inside_the_session_still_destroys_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """And arrives with its own type.

        A task group re-raises what escapes its body as an ExceptionGroup, and
        the client's reconnect handler retries an ``MCPConnectionError`` and
        nothing else, so a wrapped one would read as permanent.
        """
        harness = _Harness(monkeypatch)
        with pytest.raises(RuntimeError, match="boom"):
            await _fail_mid_session(harness)
        assert harness.container is not None
        assert harness.container.deleted

    async def test_a_start_failure_destroys_the_created_container(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The create succeeded, so something exists that nothing else reaps."""
        refusal = aiodocker.DockerError(500, "daemon refused the start")
        harness = _Harness(monkeypatch, start_error=refusal)
        with pytest.raises(MCPConnectionError, match="would not start"):
            async with harness.open():
                pass
        assert harness.container is not None
        assert harness.container.deleted

    async def test_a_create_failure_names_the_server_and_closes_the_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        absent = aiodocker.DockerError(404, "no such image")
        harness = _Harness(monkeypatch, create_error=absent)
        with pytest.raises(MCPConnectionError, match=_SERVER):
            async with harness.open():
                pass
        assert harness.docker.closed

    async def test_an_unparseable_memory_limit_refuses_before_creating(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A policy the daemon cannot be given must not launch unlimited."""
        harness = _Harness(monkeypatch)
        sandbox = MCPSandboxConfig.model_construct(
            enabled=True,
            image="example/image",
            memory_limit="not-a-size",
            pids_limit=8,
            cpus="1.0",
            network="bridge",
            deployment_id=None,
        )
        with pytest.raises(MCPConnectionError):
            async with harness.open(sandbox=sandbox):
                pass
        assert harness.containers.created_config is None
