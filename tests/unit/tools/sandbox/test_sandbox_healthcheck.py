# module-kind: tests
"""The sandbox image's own HEALTHCHECK: does the probe work, and is anyone there.

Two questions, and the second exists because asking only the first is how this
broke twice.

**Does the probe work.** The probe that shipped called ``wget``, which the Wolfi
base does not carry, so every sandbox container reported unhealthy for its whole
life (FailingStreak 30 in a measured run) and nothing noticed, because reading a
Dockerfile line tells you nothing about whether the binary in it exists. So this
runs the actual argv, against a stub server, checking the port it dials against
the port ``healthz.py`` binds.

**Is anyone there.** Those tests then all passed while every container was STILL
unhealthy for its whole life, at a measured FailingStreak of 107, because they
each start the stub server themselves and production never did: the image's CMD
is what launches ``healthz.py``, and the keep-alive container replaces that CMD.
A probe verified in isolation says nothing about whether its endpoint exists, so
the second class asserts the keep-alive starts the file the image installs.
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

from synthorg.tools.sandbox.docker_sandbox_exec import (
    _HEALTH_SERVER_PATH,
    _KEEPALIVE_ARGS,
)

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


class TestSomethingActuallyStartsTheServer:
    """The question the five tests above cannot ask.

    Every one of them exercises the probe against a stub server the TEST starts,
    which is precisely the step production never performed. The probe was correct
    and the endpoint was dead: the sandbox image's CMD is what launches
    ``healthz.py``, and the keep-alive container replaces that CMD outright, so
    every container reported unhealthy for its whole life (FailingStreak 107,
    measured on a live merge sandbox) with all five tests passing.
    """

    def test_the_keepalive_starts_the_file_the_image_installs(self) -> None:
        """The probe dials a port; something has to be listening on it."""
        installed = re.search(
            r"^COPY\s+.*?\s+docker/sandbox/healthz\.py\s+(\S+)\s*$",
            _DOCKERFILE.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        assert installed is not None, (
            "the sandbox Dockerfile no longer installs healthz.py"
        )

        assert any(installed.group(1) in argument for argument in _KEEPALIVE_ARGS), (
            "the keep-alive container replaces the image CMD, so it is the only "
            "thing that can start the health server the HEALTHCHECK dials"
        )

    def test_the_keepalive_still_holds_the_container_open(self) -> None:
        """Starting the server must not cost the container its main process."""
        script = " ".join(_KEEPALIVE_ARGS)

        assert "exec tail -f /dev/null" in script

    def test_a_foreign_image_without_the_server_still_keeps_alive(self) -> None:
        """``image_override`` runs an operator devcontainer that has no server.

        It must report no health rather than fail to start, so the launch is
        guarded on the file existing.
        """
        script = " ".join(_KEEPALIVE_ARGS)

        assert f"[ -f {_HEALTH_SERVER_PATH} ]" in script


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
