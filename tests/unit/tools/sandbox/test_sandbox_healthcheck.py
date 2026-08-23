# module-kind: tests
"""The sandbox image's own HEALTHCHECK, run rather than read.

The probe that shipped called ``wget``, which the Wolfi base does not carry, so
every sandbox container reported unhealthy for its whole life (FailingStreak 30
in a measured run) and nothing noticed, because reading a Dockerfile line tells
you nothing about whether the binary in it exists.

So this runs the actual argv. Not against a container, which would need a build
and a daemon: the probe is a self-contained Python program, and what broke was
the program, not the container. The port it dials is checked against the port
the server binds, since two literals in two files is exactly how a probe comes
to dial nothing.
"""

import http.server
import json
import re
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import override

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DOCKERFILE = _REPO_ROOT / "docker" / "sandbox" / "Dockerfile"
_HEALTHZ = _REPO_ROOT / "docker" / "sandbox" / "healthz.py"
_CMD = re.compile(r"^\s*CMD\s+(\[.*\])\s*$", re.MULTILINE)


def _probe_argv() -> list[str]:
    """Read the HEALTHCHECK argv out of the sandbox Dockerfile.

    Returns:
        The exec-form argument vector Docker would run.
    """
    match = _CMD.search(_DOCKERFILE.read_text(encoding="utf-8"))
    assert match is not None, "the sandbox Dockerfile declares no exec-form CMD"
    argv = json.loads(match.group(1))
    assert isinstance(argv, list)
    return [str(item) for item in argv]


def _served_port() -> int:
    """Read the port the health server binds.

    Returns:
        The port literal in ``healthz.py``.
    """
    bind = re.search(r'HTTPServer\(\("[^"]+",\s*(\d+)\)', _HEALTHZ.read_text("utf-8"))
    assert bind is not None, "healthz.py no longer binds a literal port"
    return int(bind.group(1))


class _Handler(http.server.BaseHTTPRequestHandler):
    """Answers /healthz the way the real server does, and nothing else."""

    def do_GET(self) -> None:
        """Serve the liveness body, or 404."""
        if self.path != "/healthz":
            self.send_error(404)
            return
        body = json.dumps({"status": "ok", "uptime_seconds": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Stay quiet under the test runner.

        The parameter name is ``BaseHTTPRequestHandler``'s own.
        """


@pytest.fixture
def stub_health_server() -> Iterator[int]:
    """Serve /healthz on an ephemeral port for the probe to dial.

    Yields:
        The bound port.
    """
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run(argv: list[str], *, port: int) -> int:
    """Run the probe against *port* instead of the container's own.

    The code string is the shipped one with only its port substituted, so what
    executes here is the probe, not a paraphrase of it.

    Returns:
        The probe's exit status.
    """
    served = _served_port()
    code = argv[-1].replace(f":{served}/", f":{port}/")
    flags = [flag for flag in argv[1:-1] if flag != "-c"]
    return subprocess.run(  # noqa: S603 -- argv is read from our own Dockerfile
        [sys.executable, *flags, "-c", code],
        capture_output=True,
        timeout=30,
        check=False,
    ).returncode


class TestTheSandboxHealthProbe:
    """It has to dial the right port, succeed when up, and fail when down."""

    def test_it_dials_the_port_the_server_binds(self) -> None:
        """Two literals in two files, which is how a probe stops reaching."""
        assert f":{_served_port()}/healthz" in _probe_argv()[-1]

    def test_it_runs_the_interpreter_that_serves_the_endpoint(self) -> None:
        """Anything else can be absent from the image, as wget was."""
        assert _probe_argv()[0] == "python3"

    def test_it_refuses_to_import_from_the_working_directory(self) -> None:
        """WORKDIR is the agent-writable workspace.

        Without isolated mode ``python3 -c`` puts the cwd at the head of
        ``sys.path``, so an agent dropping a ``urllib.py`` into its own
        workspace gets it executed on the next probe tick.
        """
        assert "-I" in _probe_argv()

    def test_it_succeeds_against_a_served_endpoint(
        self, stub_health_server: int
    ) -> None:
        """The case that was broken for the life of every container."""
        assert _run(_probe_argv(), port=stub_health_server) == 0

    def test_it_fails_when_nothing_is_listening(self, unused_port: int) -> None:
        """A probe that passes regardless reports nothing at all."""
        assert _run(_probe_argv(), port=unused_port) != 0


@pytest.fixture
def unused_port() -> int:
    """Return a port nothing is listening on.

    Taken by binding and releasing rather than by picking a number, so it
    cannot collide with whatever else this machine is running.

    Returns:
        A port with no listener.
    """
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = int(server.server_address[1])
    server.server_close()
    return port
