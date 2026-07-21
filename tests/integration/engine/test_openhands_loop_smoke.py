"""Live end-to-end smoke for the OpenHands inner execution loop.

Runs one real OpenHands conversation through the full boundary stack: the
adapter drives the in-sandbox ``agent_server`` (the real ``openhands-sdk``
factory), which reaches models only through the LLM gateway and credentialed
tools only through the credentialed-MCP endpoint. It asserts the run
completes and produces work turns.

Gated: this needs a provisioned live stack (a running gateway + cred-MCP on
a sandbox-reachable address, the OpenHands image, and ``openhands-sdk``
installed in-image), so it is skipped unless ``SYNTHORG_OPENHANDS_SMOKE=1``
and the endpoint env vars are set. It never runs in the default unit/CI
tiers, where the loop logic is covered by the offline fake in
``tests/unit/engine/openhands/test_loop.py``.

Required env when enabled:
    SYNTHORG_OPENHANDS_SMOKE=1
    SYNTHORG_OPENHANDS_GATEWAY_URL   the sandbox-reachable gateway base URL
    SYNTHORG_OPENHANDS_MCP_URL       the sandbox-reachable cred-MCP base URL
    SYNTHORG_OPENHANDS_SIGNER_SECRET the shared gateway signer secret (>=32B)
"""

import os
from typing import Final

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


def _smoke_ready() -> bool:
    """Whether the live OpenHands stack env is fully configured.

    Returns:
        ``True`` when the smoke is enabled and every endpoint/secret is set
        and the SDK is importable in-process.
    """
    if os.environ.get(_ENABLE_VAR) != "1":
        return False
    if not all(os.environ.get(v) for v in (_GATEWAY_VAR, _MCP_VAR, _SECRET_VAR)):
        return False
    try:
        import openhands.sdk  # noqa: F401
    except ImportError:
        return False
    return True


skip_no_stack = pytest.mark.skipif(
    not _smoke_ready(),
    reason=(
        f"set {_ENABLE_VAR}=1 with a live gateway + cred-MCP + OpenHands image "
        "and openhands-sdk installed to run the live smoke"
    ),
)


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
        build_conversation=_sdk_factory(),
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


def _sdk_factory() -> ConversationFactory:
    """Return the real SDK conversation factory.

    Imported lazily so module collection never touches the SDK on hosts
    where it is absent (the skip guard has already vetted availability).

    Returns:
        The ``build_sdk_conversation`` factory callable.
    """
    from synthorg.engine.openhands.sdk_runtime import build_sdk_conversation

    return build_sdk_conversation
