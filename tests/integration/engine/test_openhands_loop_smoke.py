"""Live end-to-end smoke for the OpenHands inner execution loop.

Runs one real OpenHands conversation through the full boundary stack: the
adapter spawns the OpenHands sandbox container (egress pinned to the gateway
+ cred-MCP hosts), feeds the run spec on stdin and consumes the normalized
event stream from stdout. The in-container agent reaches models only through
the LLM gateway and credentialed tools only through the credentialed-MCP
endpoint. It asserts the run completes and produces work turns.

Gated: this needs a provisioned live stack (a running gateway + cred-MCP on
a sandbox-reachable address, the built OpenHands image, and a reachable
Docker daemon), so it is skipped unless ``SYNTHORG_OPENHANDS_SMOKE=1`` and
the endpoint env vars are set. It never runs in the default unit/CI tiers,
where the loop logic is covered by the offline fake in
``tests/unit/engine/openhands/test_loop.py`` and the container runtime's
normalization by ``test_container_runtime.py``.

Required env when enabled:
    SYNTHORG_OPENHANDS_SMOKE=1
    SYNTHORG_OPENHANDS_GATEWAY_URL   the sandbox-reachable gateway base URL
    SYNTHORG_OPENHANDS_MCP_URL       the sandbox-reachable cred-MCP base URL
    SYNTHORG_OPENHANDS_SIGNER_SECRET the shared gateway signer secret (>=32B)
    SYNTHORG_OPENHANDS_IMAGE         the built OpenHands sandbox image ref
    SYNTHORG_OPENHANDS_WORKSPACE     an existing host workspace root to mount
"""

import functools
import os
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.clock import SystemClock
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.openhands.config import OpenHandsLoopConfig, OpenHandsLoopDeps
from synthorg.engine.openhands.conversation import ConversationFactory
from synthorg.engine.openhands.loop import OpenHandsLoop
from synthorg.llm.gateway_token import GatewaySigner
from synthorg.providers.protocol import CompletionProvider
from tests._shared import mock_of
from tests._shared.scripted_provider import make_e2e_identity, make_e2e_task

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]

_ENABLE_VAR: Final[str] = "SYNTHORG_OPENHANDS_SMOKE"
_GATEWAY_VAR: Final[str] = "SYNTHORG_OPENHANDS_GATEWAY_URL"
_MCP_VAR: Final[str] = "SYNTHORG_OPENHANDS_MCP_URL"
_SECRET_VAR: Final[str] = "SYNTHORG_OPENHANDS_SIGNER_SECRET"
_IMAGE_VAR: Final[str] = "SYNTHORG_OPENHANDS_IMAGE"
_WORKSPACE_VAR: Final[str] = "SYNTHORG_OPENHANDS_WORKSPACE"

_REQUIRED_VARS: Final[tuple[str, ...]] = (
    _GATEWAY_VAR,
    _MCP_VAR,
    _SECRET_VAR,
    _IMAGE_VAR,
    _WORKSPACE_VAR,
)


def _smoke_ready() -> bool:
    """Whether the live OpenHands stack env is fully configured.

    Returns:
        ``True`` when the smoke is enabled and every endpoint / secret /
        image / workspace is set.
    """
    if os.environ.get(_ENABLE_VAR) != "1":
        return False
    return all(os.environ.get(v) for v in _REQUIRED_VARS)


skip_no_stack = pytest.mark.skipif(
    not _smoke_ready(),
    reason=(
        f"set {_ENABLE_VAR}=1 with a live gateway + cred-MCP + built OpenHands "
        "image + a reachable Docker daemon to run the live smoke"
    ),
)


def _host_port(url: str) -> str:
    """Extract ``host:port`` from a URL, inferring the scheme default port.

    Returns:
        The ``host:port`` string.
    """
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.hostname}:{port}"


def _container_factory() -> ConversationFactory:
    """Build the container-backed conversation factory for the live run.

    Constructs the egress-pinned :class:`DockerSandbox` (OpenHands image,
    bridge network, allowlist locked to the gateway + MCP hosts, workspace
    mounted read-write) and binds the container runtime to it.

    Returns:
        The ``build_container_conversation`` factory bound to the sandbox.
    """
    from synthorg.engine.openhands.container_runtime import (
        build_container_conversation,
    )
    from synthorg.tools.sandbox._mount_mode import MountMode
    from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
    from synthorg.tools.sandbox.docker_sandbox import DockerSandbox

    allowed_hosts = tuple(
        sorted(
            {
                _host_port(os.environ[_GATEWAY_VAR]),
                _host_port(os.environ[_MCP_VAR]),
            }
        )
    )
    config = DockerSandboxConfig(
        image=os.environ[_IMAGE_VAR],
        network="bridge",
        allowed_hosts=allowed_hosts,
        mount_mode=MountMode.READ_WRITE,
    )
    sandbox = DockerSandbox(
        config=config,
        workspace=Path(os.environ[_WORKSPACE_VAR]).resolve(),
        clock=SystemClock(),
    )
    return functools.partial(build_container_conversation, sandbox, 600.0, 3600.0)


def _bound_agent() -> AgentIdentity:
    """Build a provider-bound agent identity for the smoke run.

    Returns:
        An :class:`AgentIdentity` bound to a concrete ``(provider, model)``.
    """
    return make_e2e_identity(label="openhands-smoke")


def _work_task(identity: AgentIdentity) -> Task:
    """Build a task that expects a code artifact.

    Returns:
        A :class:`Task` declaring an expected code deliverable.
    """
    task = make_e2e_task(
        identity=identity,
        title="Write a hello module",
        description="Create src/hello.py exposing greet() returning 'hello'.",
        label="openhands-smoke-task",
    )
    return task.model_copy(
        update={
            "artifacts_expected": (
                ExpectedArtifact(type=ArtifactType.CODE, path="src/hello.py"),
            )
        }
    )


@skip_no_stack
async def test_openhands_live_run_produces_work() -> None:
    """One real OpenHands run completes and produces work turns."""
    deps = OpenHandsLoopDeps(
        build_conversation=_container_factory(),
        signer=GatewaySigner(
            secret=os.environ[_SECRET_VAR].encode(), clock=SystemClock()
        ),
        gateway_base_url=os.environ[_GATEWAY_VAR],
        mcp_base_url=os.environ[_MCP_VAR],
        clock=SystemClock(),
    )
    loop = OpenHandsLoop(config=OpenHandsLoopConfig(), deps=deps)
    agent = _bound_agent()
    ctx = AgentContext.from_identity(agent, task=_work_task(agent))

    result = await loop.execute(context=ctx, provider=mock_of[CompletionProvider]())

    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.total_tool_calls > 0
