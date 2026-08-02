"""Container contract test for the OpenHands loop image, with zero LLM spend.

Runs the real ``docker/openhands`` image against a local OpenAI-compatible stub
and asserts the wire contract the host adapter depends on:

- the run spec is read as one JSON line from stdin and parsed;
- stdout carries ONLY normalized events, one JSON object per line, and every
  adapter-relevant kind appears;
- the container's running accumulated cost advances, and the per-turn deltas
  ``container_runtime._parse_event`` derives sum to the run total;
- the run reaches a terminal state and leaves no container behind;
- an induced failure never echoes the per-run gateway bearer.

The spec is serialised with the production ``_spec_line`` and every stdout line
is parsed with the production ``_parse_event``, so a drift between the image and
the adapter fails here rather than in a live run. The event-kind assertions are
also the drift detector for ``run_task.py::_normalize``, which maps SDK events
by ``isinstance`` against four classes: a rename upstream shows up as a missing
kind instead of a line on stderr nobody reads.

No provider is contacted: the stub answers every completion locally, so the run
costs nothing. It needs a Docker daemon and the built image, named by
``SYNTHORG_OPENHANDS_IMAGE``; without either the test class skips.
"""

import contextlib
import json
import os
import shutil
import subprocess
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar, Final, override

import pytest

from synthorg.engine.openhands.container_runtime import _parse_event, _spec_line
from synthorg.engine.openhands.conversation import OpenHandsRunSpec
from synthorg.engine.openhands.events import OpenHandsEvent, OpenHandsEventKind

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(600)]

_IMAGE_VAR: Final[str] = "SYNTHORG_OPENHANDS_IMAGE"
_IMAGE: Final[str] = os.environ.get(_IMAGE_VAR, "")
# A distinctive literal so the scrub assertion cannot pass by coincidence.
_BEARER: Final[str] = "synthorg-contract-bearer-2Zq7Z0KpV1nW"
_TOOL: Final[str] = "terminal"
_MARKER: Final[str] = "synthorg-contract-ok"
# Per-completion cost the stub bills through the LiteLLM proxy cost header, so
# the accumulated total advances by a known amount on every turn.
_COST_PER_CALL: Final[float] = 0.25
_RUN_TIMEOUT_SECONDS: Final[float] = 480.0
# Bounds the short docker CLI calls (remove, inspect) independently of the
# run budget, so a wedged daemon is reported rather than silently absorbed.
_DOCKER_CLI_TIMEOUT_SECONDS: Final[float] = 60.0
# Bound through a name, not a literal in the argv list, so the lint rule against
# partial executable paths sees a resolved value (as tests/integration/
# test_web_image.py does for the same reason).
_DOCKER: Final[str] = "docker"
_NAME_PREFIX: Final[str] = "synthorg-oh-contract-"

skip_no_image = pytest.mark.skipif(
    not _IMAGE or shutil.which("docker") is None,
    reason=f"needs a Docker daemon and {_IMAGE_VAR} naming a built OpenHands image",
)


class _StubHandler(BaseHTTPRequestHandler):
    """An OpenAI-compatible completions endpoint plus a minimal MCP server.

    The agent will not build without an MCP endpoint to connect to, so the two
    boundaries the loop governs are both served here, from one port, and the
    egress allowlist a real deployment derives collapses to that single host.
    """

    protocol_version = "HTTP/1.1"
    # Bound the read so a handler thread parked on an idle keep-alive socket
    # unblocks itself. ThreadingHTTPServer.server_close() joins every handler
    # thread with no timeout, so a thread waiting on a peer that never closes
    # hangs teardown until the whole test times out.
    timeout = 5
    # Rebound per test by the fixture, on a fresh subclass, so recorded calls
    # never leak between tests.
    calls: ClassVar[list[dict[str, str]]] = []
    completions: ClassVar[list[int]] = []
    # Guards the read-modify-use on `completions`: append-then-len across
    # concurrent handler threads can hand two of them the same turn number.
    lock: ClassVar[threading.Lock] = threading.Lock()
    port: int = 0

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Silence the default stderr access log."""

    def _respond(self, code: int, body: bytes, *, cost: float | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Close after every response so a handler thread never parks waiting
        # for a request that will not come. This stub needs no pooling, and
        # server_close() joins handler threads unconditionally.
        self.send_header("Connection", "close")
        self.close_connection = True
        # Every response carries a session id: the MCP client reads it from the
        # initialize response and replays it on later calls.
        self.send_header("Mcp-Session-Id", "synthorg-contract-session")
        if cost is not None:
            # How a LiteLLM-proxy-shaped endpoint reports spend. Without it the
            # SDK cannot price an unmapped model and every event reports zero,
            # which would make the delta assertion vacuous.
            self.send_header("x-litellm-response-cost", str(cost))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        """Refuse the optional MCP server-push stream and LiteLLM's model probe."""
        self._respond(405, b"")

    def do_DELETE(self) -> None:
        """Accept the MCP session teardown."""
        self._respond(200, b"{}")

    def do_POST(self) -> None:
        """Route a completion or an MCP JSON-RPC call."""
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        with self.lock:
            self.calls.append(
                {"path": self.path, "auth": self.headers.get("Authorization", "")}
            )
        if self.path.endswith("chat/completions"):
            self._completion(raw)
            return
        self._mcp(raw)

    def _completion(self, raw: bytes) -> None:
        """Answer a completion: a tool call first, then a closing message."""
        request = json.loads(raw or b"{}")
        with self.lock:
            self.completions.append(1)
            turn = len(self.completions)
        advertised = {
            tool.get("function", {}).get("name") for tool in request.get("tools", [])
        }
        if turn == 1 and _TOOL in advertised:
            message: dict[str, object] = {
                "role": "assistant",
                "content": "running one command",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": _TOOL,
                            "arguments": json.dumps({"command": f"echo {_MARKER}"}),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": "done"}
            finish_reason = "stop"
        body = json.dumps(
            {
                "id": f"contract-{turn}",
                "object": "chat.completion",
                "created": 1,
                "model": request.get("model", "stub"),
                "choices": [
                    {"index": 0, "message": message, "finish_reason": finish_reason}
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        ).encode()
        self._respond(200, body, cost=_COST_PER_CALL * turn)

    def _mcp(self, raw: bytes) -> None:
        """Serve the three JSON-RPC methods the client needs to finish setup."""
        request = json.loads(raw or b"{}")
        request_id = request.get("id")
        if request_id is None:
            # A notification (``notifications/initialized``) takes no response.
            self._respond(202, b"")
            return
        if request.get("method") == "initialize":
            params = request.get("params") or {}
            result: dict[str, object] = {
                # Echo the client's version rather than pinning one, so an SDK
                # bump renegotiating a newer revision does not fail the test.
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "synthorg-contract-stub", "version": "0"},
            }
        elif request.get("method") == "tools/list":
            result = {"tools": []}
        else:
            result = {}
        payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
        self._respond(200, json.dumps(payload).encode())


@contextlib.contextmanager
def _serving_stub() -> Iterator[type[_StubHandler]]:
    """Serve the gateway + MCP stub on an ephemeral port.

    Yields:
        A fresh handler subclass carrying ``port``, ``calls`` and
        ``completions``, so recorded calls never leak between users.
    """

    # Declared here, not at module scope: a class body evaluated per call gets
    # its own list objects, so one user's recorded calls can never be read by
    # the next.
    class _BoundStub(_StubHandler):
        calls: ClassVar[list[dict[str, str]]] = []
        completions: ClassVar[list[int]] = []

    server = ThreadingHTTPServer(("0.0.0.0", 0), _BoundStub)  # noqa: S104
    _BoundStub.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _BoundStub
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@pytest.fixture
def stub() -> Iterator[type[_StubHandler]]:
    """Serve the stub for one test that needs its own run.

    Yields:
        The bound handler class.
    """
    with _serving_stub() as handler:
        yield handler


_HappyRun = tuple[type[_StubHandler], "subprocess.CompletedProcess[str]"]


@pytest.fixture(scope="class")
def happy_run() -> Iterator[_HappyRun]:
    """Run the container once and share the result across the happy-path tests.

    These assert different facets of one deterministic run (same stub, same
    spec), and each container launch costs ~30s, so re-deriving the same
    output per assertion buys nothing.

    Yields:
        The stub that served the run, and the finished process.
    """
    with _serving_stub() as handler:
        yield handler, _run_container(_run_spec(handler.port))


def _run_spec(port: int, *, gateway_port: int | None = None) -> OpenHandsRunSpec:
    """Build a run spec addressing the stub through the host-gateway alias.

    Args:
        port: The stub's port, used for the credentialed-MCP endpoint.
        gateway_port: Overrides the gateway port, to point that leg at a
            closed port without disturbing MCP setup.

    Returns:
        The spec the container reads from stdin.
    """
    host = f"http://host.docker.internal:{gateway_port or port}"
    return OpenHandsRunSpec(
        task_prompt="say hi",
        model="example-large-001",
        gateway_base_url=f"{host}/api/v1/gateway/v1",
        gateway_token=_BEARER,
        mcp_base_url=f"http://host.docker.internal:{port}/api/v1/mcp-gateway",
        workspace_path="/workspace",
        conversation_id=uuid.uuid4(),
        max_turns=4,
    )


def _run_container(spec: OpenHandsRunSpec) -> subprocess.CompletedProcess[str]:
    """Drive one container run, feeding the production spec line on stdin.

    Returns:
        The finished process, with stdout carrying the event stream.
    """
    return _run_container_with_input(_spec_line(spec))


def _run_container_with_input(stdin: str) -> subprocess.CompletedProcess[str]:
    """Drive one container run with arbitrary stdin.

    Returns:
        The finished process, with stdout carrying the event stream.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw")
    name = f"{_NAME_PREFIX}{worker}-{os.getpid()}"
    _force_remove(name)
    try:
        return subprocess.run(  # noqa: S603
            [
                _DOCKER,
                "run",
                "-i",
                "--rm",
                "--name",
                name,
                # The alias the loop wiring gives every OpenHands container, and
                # the reason the shipped compose endpoint defaults resolve.
                "--add-host=host.docker.internal:host-gateway",
                _IMAGE,
            ],
            input=stdin,
            capture_output=True,
            text=True,
            # The SDK renders box-drawing characters; the platform default
            # codec cannot decode them and would fail the read, not the run.
            encoding="utf-8",
            errors="replace",
            timeout=_RUN_TIMEOUT_SECONDS,
            check=False,
        )
    finally:
        _force_remove(name)


def _force_remove(name: str) -> None:
    """Remove a container, bounded so a wedged daemon fails attributably.

    Without its own timeout this inherits only the whole-test deadline, and a
    hard kill there skips the cleanup it was meant to guarantee.
    """
    subprocess.run(  # noqa: S603
        [_DOCKER, "rm", "-f", name],
        capture_output=True,
        check=False,
        timeout=_DOCKER_CLI_TIMEOUT_SECONDS,
    )


def _events(stdout: str) -> list[OpenHandsEvent]:
    """Parse the container's stdout with the production event codec.

    An ``unmapped`` line is a valid protocol line the adapter deliberately
    forwards nothing for, so it is skipped rather than treated as a parse
    failure. ``_unmapped`` is the assertion that catches those.

    Returns:
        Every event the adapter would forward to the loop, in order.
    """
    parsed: list[OpenHandsEvent] = []
    accumulated = 0.0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        event, accumulated = _parse_event(line, accumulated)
        if event is None:
            assert _is_unmapped(line), (
                f"adapter could not parse container line: {line!r}"
            )
            continue
        parsed.append(event)
    return parsed


def _is_unmapped(line: str) -> bool:
    """Report whether a line is the container's known-skew marker.

    Returns:
        ``True`` when the line carries ``kind: unmapped``.
    """
    try:
        return bool(json.loads(line).get("kind") == "unmapped")
    except ValueError:
        return False


def _unmapped(stdout: str) -> list[str]:
    """Name every SDK event the container could not classify.

    Returns:
        The class names reported under the ``unmapped`` kind.
    """
    return [
        str(json.loads(line).get("text", ""))
        for line in stdout.splitlines()
        if line.strip() and _is_unmapped(line)
    ]


def _totals(stdout: str) -> Iterator[float]:
    """Yield the running accumulated cost each event line reports.

    Yields:
        The ``cost`` field of every event line, in order.
    """
    for line in stdout.splitlines():
        if line.strip():
            yield float(json.loads(line).get("cost", 0.0))


@skip_no_image
class TestContainerContract:
    def test_run_spec_parses_and_reaches_the_gateway(
        self,
        happy_run: tuple[type[_StubHandler], subprocess.CompletedProcess[str]],
    ) -> None:
        stub, result = happy_run

        assert "empty run spec on stdin" not in result.stdout
        assert result.returncode == 0, result.stderr[-2000:]
        # Reaching a completion at all proves the whole spec was honoured: the
        # gateway URL, the bearer, and a model id LiteLLM could route.
        assert stub.completions, "the container never called the gateway"
        completions = [c for c in stub.calls if c["path"].endswith("chat/completions")]
        assert completions, "no completion request recorded"
        assert all(c["auth"] == f"Bearer {_BEARER}" for c in completions)
        mcp = [c for c in stub.calls if c["path"].endswith("/mcp")]
        assert mcp, "the container never reached the credentialed-MCP endpoint"
        assert all(c["auth"] == f"Bearer {_BEARER}" for c in mcp)

    def test_stdout_carries_only_normalized_events(
        self,
        happy_run: tuple[type[_StubHandler], subprocess.CompletedProcess[str]],
    ) -> None:
        _, result = happy_run

        assert result.returncode == 0, result.stderr[-2000:]
        # An unmapped line means the SDK emitted an event class the container
        # neither forwards nor knows to ignore, i.e. the SDK grew or renamed
        # one. That is the drift this test exists to catch: classify the named
        # event in run_task._IGNORED_EVENT_NAMES or map it in _normalize.
        assert _unmapped(result.stdout) == [], (
            f"unclassified SDK events: {_unmapped(result.stdout)}"
        )
        # _events() asserts every single line parses, which is the real claim:
        # stdout is a protocol, so any prose on it is a defect.
        kinds = [event.kind for event in _events(result.stdout)]
        assert kinds[-1] is OpenHandsEventKind.FINISHED
        # One per adapter-relevant class run_task._normalize maps by isinstance.
        for expected in (
            OpenHandsEventKind.MESSAGE,
            OpenHandsEventKind.ACTION,
            OpenHandsEventKind.OBSERVATION,
        ):
            assert expected in kinds, f"{expected} missing from {kinds}"
        actions = [e for e in _events(result.stdout) if e.tool_name]
        assert [e.tool_name for e in actions] == [_TOOL]

    def test_cost_deltas_sum_to_the_run_total(
        self,
        happy_run: tuple[type[_StubHandler], subprocess.CompletedProcess[str]],
    ) -> None:
        _, result = happy_run

        assert result.returncode == 0, result.stderr[-2000:]
        totals = list(_totals(result.stdout))
        assert totals[-1] > 0.0, "the container reported no spend at all"
        assert totals == sorted(totals), f"accumulated cost went backwards: {totals}"
        # The adapter forwards a per-turn DELTA, so the deltas must reconstruct
        # the total the container finished on; a flat or double-counted series
        # would misattribute every turn's cost downstream.
        assert sum(event.cost for event in _events(result.stdout)) == pytest.approx(
            totals[-1]
        )

    def test_run_terminates_and_leaves_no_container(
        self,
        happy_run: tuple[type[_StubHandler], subprocess.CompletedProcess[str]],
    ) -> None:
        _, result = happy_run

        assert result.returncode == 0
        # Scoped to THIS worker's container name, not the shared prefix: under
        # xdist a sibling worker's in-flight container would otherwise read as
        # a leak from this test.
        worker = os.environ.get("PYTEST_XDIST_WORKER", "gw")
        survivors = subprocess.run(  # noqa: S603
            [
                _DOCKER,
                "ps",
                "-a",
                "--filter",
                f"name={_NAME_PREFIX}{worker}-{os.getpid()}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=_DOCKER_CLI_TIMEOUT_SECONDS,
        )
        assert survivors.stdout.strip() == "", survivors.stdout

    def test_empty_stdin_fails_fast_instead_of_hanging(self) -> None:
        # The host keeps stdin attached for the container's lifetime rather
        # than half-closing it, so a regression to "read until EOF" would leak
        # a running container instead of failing. Nothing else bounds it.
        result = _run_container_with_input("")

        assert result.returncode == 1
        assert "empty run spec" in result.stdout

    def test_malformed_stdin_reports_a_coherent_error(self) -> None:
        # The only path where the bearer is still unknown when the scrubber
        # runs. Without its empty-secret guard, replace("", "***") would wedge
        # "***" between every character of the message.
        result = _run_container_with_input("{not json\n")

        assert result.returncode == 1
        errors = [
            e for e in _events(result.stdout) if e.kind is OpenHandsEventKind.ERROR
        ]
        assert errors, f"expected an error event, got {result.stdout!r}"
        assert "***" not in errors[0].text

    def test_failure_never_echoes_the_run_bearer(
        self, stub: type[_StubHandler]
    ) -> None:
        # Port 9 (discard) refuses, so the SDK raises with the gateway request
        # in its message; MCP still points at the stub so the failure lands in
        # the LLM leg rather than before the bearer is ever used.
        spec = _run_spec(stub.port, gateway_port=9)

        result = _run_container(spec)

        assert result.returncode == 1
        errors = [
            e for e in _events(result.stdout) if e.kind is OpenHandsEventKind.ERROR
        ]
        assert errors, f"expected an error event, got {result.stdout!r}"
        assert _BEARER not in result.stdout
        assert _BEARER not in result.stderr
