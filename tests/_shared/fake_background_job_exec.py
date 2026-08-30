"""A scripted ``aiodocker`` client double for the background-job wrapper.

Answers ``container.exec()`` by inspecting the wrapper's own built
script text (rather than by call order or argument position), so a
test can assert on behaviour without knowing the exact shell it runs.
Two suites had grown their own copy of this and the underlying
in-memory job repository; both live here now, next to each other.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

_DOCKER_MODULE = "synthorg.tools.sandbox.docker_sandbox.aiodocker"

__all__ = ["make_mock_docker", "patch_aiodocker", "responder_for"]


class _FakeExecMessage:
    """Mimics ``aiodocker.stream.Message`` (``stream``/``data``)."""

    def __init__(self, stream: int, data: bytes) -> None:
        self.stream = stream
        self.data = data


def _make_exec_stream(*, stdout: bytes) -> MagicMock:
    """Build a fake aiodocker exec ``Stream`` yielding one stdout frame then EOF."""
    stream = MagicMock()
    frames: list[_FakeExecMessage | None] = []
    if stdout:
        frames.append(_FakeExecMessage(1, stdout))
    frames.append(None)
    stream.read_out = AsyncMock(side_effect=frames)
    stream.close = AsyncMock()
    return stream


def _install_scripted_exec(
    container_obj: MagicMock, responder: Callable[[str], bytes]
) -> None:
    """Wire ``container.exec()`` to answer based on the script it was given.

    *responder* receives the joined ``cmd`` argv (the wrapper's own
    built script text lives in the last element) and returns the
    stdout bytes to yield.
    """

    def _new_exec(*_args: object, **kwargs: object) -> MagicMock:
        cmd = kwargs.get("cmd") or ()
        cmd_seq = cmd if isinstance(cmd, list | tuple) else ()
        script = str(cmd_seq[-1]) if cmd_seq else ""
        exec_obj = MagicMock()
        exec_obj.start = MagicMock(
            return_value=_make_exec_stream(stdout=responder(script))
        )
        exec_obj.inspect = AsyncMock(return_value={"ExitCode": 0})
        return exec_obj

    container_obj.exec = AsyncMock(side_effect=_new_exec)


def make_mock_docker(responder: Callable[[str], bytes]) -> MagicMock:
    """Create a mock aiodocker.Docker client scripted by *responder*.

    Returns:
        The scripted client double.
    """
    mock_docker = MagicMock()
    mock_docker.version = AsyncMock(return_value={"ApiVersion": "1.43"})
    mock_docker.close = AsyncMock()

    mock_containers = MagicMock()
    mock_docker.containers = mock_containers

    mock_created_container = MagicMock()
    mock_created_container.id = "abc123def456"
    mock_containers.create = AsyncMock(return_value=mock_created_container)

    mock_container_obj = MagicMock()
    mock_container_obj.start = AsyncMock()
    mock_container_obj.show = AsyncMock(return_value={"State": {"Running": True}})
    mock_container_obj.stop = AsyncMock()
    mock_container_obj.delete = AsyncMock()
    _install_scripted_exec(mock_container_obj, responder)

    mock_containers.container = MagicMock(return_value=mock_container_obj)
    return mock_docker


@contextmanager
def patch_aiodocker(mock_docker: MagicMock) -> Iterator[MagicMock]:
    """Patch the Docker sandbox's own ``aiodocker`` import with *mock_docker*."""
    mock_module = MagicMock()
    mock_module.Docker = MagicMock(return_value=mock_docker)
    with patch(_DOCKER_MODULE, mock_module) as p:
        yield p


def responder_for(pid: str = "4242") -> Callable[[str], bytes]:
    """Return a responder answering pid-confirm / RUNNING / anything else empty.

    Returns:
        A callable matching :func:`make_mock_docker`'s own responder shape.
    """

    def _respond(script: str) -> bytes:
        if "child_pid=$!" in script:
            return f"{pid}\n".encode()
        if 'echo "RUNNING"' in script:
            return b"RUNNING\n"
        return b""

    return _respond
