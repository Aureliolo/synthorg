"""Acceptance: direct MCP acting under trust, sensitive gating.

Drives the REAL :meth:`AgentEngine.run_chat_action` over a deterministic
``ScriptedDriver`` (zero LLM spend), the REAL agent -> SynthOrg-MCP
self-consumer (``build_mcp_self_consumer`` in ``TRUST_SCOPED`` mode), and
the REAL consent dispatcher (:func:`signal_resume_intent` Flow 1) wired
over an ``AppState``. The park-side and resume-side share one
``ApprovalStore`` instance and one boot ``AgentEngine`` (the worker
execution service holds the same engine that parked), so a passing
resume proves the chain end to end rather than over isolated doubles.

The acceptance bar:

- A non-sensitive instruction performs a real MCP action: a tool sourced
  from the self-consumer is composed into the governed invoker and
  executes, completing the chat action with no parking.
- A sensitive action (the agent self-requests human approval) parks a
  ``PARKED_CONTEXT`` approval with no side effect; ``signal_resume_intent``
  (approve) routes through Flow 1 -> taskless resume and the now-authorised
  work completes.
- Reject leaves no side effect: the resumed agent stands down and the
  gated work never runs.
"""

from collections.abc import AsyncGenerator
from typing import NamedTuple

import pytest
from pydantic import JsonValue

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._approval_review_gate import signal_resume_intent
from synthorg.api.state import AppState
from synthorg.core.agent import AgentIdentity, ToolPermissions
from synthorg.core.enums import ApprovalSource, ToolAccessLevel
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.mcp_self_consumer import build_mcp_self_consumer
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.drivers.scripted import (
    ScriptedDriver,
    SequencedResponseStrategy,
)
from synthorg.providers.models import CompletionResponse, ToolCall
from synthorg.security.config import McpSelfConsumerConfig, McpSelfConsumerMode
from synthorg.tools.registry import ToolRegistry
from synthorg.workers.execution_service import AgentEngineExecutionService
from tests._shared import make_app_state
from tests._shared.scripted_provider import (
    make_e2e_identity,
    make_text_response,
    make_tool_call_response,
)
from tests.unit.api.fakes import FakePersistenceBackend
from tests.unit.engine.chat_action_fakes import InMemoryParkedRepo, QueryTool

pytestmark = pytest.mark.e2e

_READ_TOOL = "synthorg_tasks_list"
_ADMIN_TOOL = "synthorg_agents_delete"
_APPROVAL_ARGS: dict[str, JsonValue] = {
    "action_type": "deploy:service",
    "title": "Deploy to prod",
    "description": "Ship the release to production.",
}


@pytest.fixture
async def persistence() -> AsyncGenerator[FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def task_engine(
    persistence: FakePersistenceBackend,
) -> AsyncGenerator[TaskEngine]:
    engine = TaskEngine(persistence=persistence)
    await engine.start()
    yield engine
    await engine.stop()


def _acting_identity(
    *,
    access_level: ToolAccessLevel = ToolAccessLevel.STANDARD,
    allowed: tuple[str, ...] = (),
) -> AgentIdentity:
    """An acting agent at *access_level*, optionally name-allowlisted."""
    return make_e2e_identity(
        tools=ToolPermissions(
            access_level=access_level,
            allowed=allowed,
        ),
    )


class _Bundle(NamedTuple):
    """The wired runtime under test (shared store + one boot engine)."""

    engine: AgentEngine
    service: AgentEngineExecutionService
    app_state: AppState
    store: ApprovalStore
    repo: InMemoryParkedRepo
    identity: AgentIdentity


class _Wiring(NamedTuple):
    """Per-scenario governance knobs for the wired runtime.

    ``access_level`` is the agent's earned trust; ``allowlist`` is the
    operator's org-wide self-consumer exposure; ``identity_allowed`` is
    the per-agent name grant that bypasses category gating;
    ``extra_tools`` are non-MCP tools registered directly on the engine.
    """

    access_level: ToolAccessLevel = ToolAccessLevel.STANDARD
    allowlist: tuple[str, ...] = ()
    identity_allowed: tuple[str, ...] = ()
    extra_tools: tuple[QueryTool, ...] = ()


_DEFAULT_WIRING = _Wiring()


async def _build_bundle(
    task_engine: TaskEngine,
    *,
    responses: tuple[CompletionResponse, ...],
    wiring: _Wiring = _DEFAULT_WIRING,
) -> _Bundle:
    """Wire the engine, self-consumer, worker service, and app state.

    The ``AppState`` is built first (the self-consumer binds it), then the
    engine, then the worker service is installed back onto the state via
    the once-only boot seam -- mirroring the production boot ordering so
    ``signal_resume_intent`` resolves the worker the same way startup does.

    Returns:
        The assembled :class:`_Bundle`.
    """
    store = ApprovalStore()
    repo = InMemoryParkedRepo()
    registry = AgentRegistryService()
    identity = _acting_identity(
        access_level=wiring.access_level,
        allowed=wiring.identity_allowed,
    )
    await registry.register(identity)

    app_state = make_app_state(
        approval_store=store,
        task_engine=task_engine,
        agent_registry=registry,
    )
    self_consumer = build_mcp_self_consumer(
        McpSelfConsumerConfig(
            mode=McpSelfConsumerMode.TRUST_SCOPED,
            read_tool_allowlist=wiring.allowlist,
        ),
        app_state,
    )
    provider = ScriptedDriver(
        "test-provider",
        strategy=SequencedResponseStrategy(responses),
    )
    engine = AgentEngine(
        provider=provider,
        tool_registry=ToolRegistry(list(wiring.extra_tools)),
        approval_store=store,
        parked_context_repo=repo,  # type: ignore[arg-type]
        mcp_self_consumer=self_consumer,
    )
    service = AgentEngineExecutionService(
        engine=engine,
        task_engine=task_engine,
        agent_registry=registry,
    )
    app_state.set_worker_execution_service(service)
    return _Bundle(
        engine=engine,
        service=service,
        app_state=app_state,
        store=store,
        repo=repo,
        identity=identity,
    )


class TestDirectMcpActionE2E:
    """A chat instruction drives a real, trust-scoped MCP action."""

    async def test_elevated_agent_action_completes(
        self,
        task_engine: TaskEngine,
    ) -> None:
        # A non-sensitive instruction: an ELEVATED-trust agent calls a real
        # MCP read tool sourced from the self-consumer, then summarises. At
        # ELEVATED the self-consumer exposes the full surface and the
        # ToolPermissionChecker permits the MCP category, so the governed
        # invoker composes and runs the bridge tool end to end.
        bundle = await _build_bundle(
            task_engine,
            responses=(
                make_tool_call_response(
                    tool_calls=(ToolCall(id="c1", name=_READ_TOOL, arguments={}),),
                ),
                make_text_response("There are no open tasks."),
            ),
            wiring=_Wiring(access_level=ToolAccessLevel.ELEVATED),
        )

        result = await bundle.engine.run_chat_action(
            identity=bundle.identity,
            instruction="List the open tasks.",
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert not result.parked
        assert result.final_message == "There are no open tasks."
        executed = [tc for tc in result.tool_calls if tc.tool_name == _READ_TOOL]
        assert len(executed) == 1, "the self-consumer MCP tool did not run"
        assert not executed[0].is_error

    async def test_operator_granted_standard_agent_action_completes(
        self,
        task_engine: TaskEngine,
    ) -> None:
        # Two-layer governance for a low-trust agent: the operator both
        # exposes the read tool org-wide (self-consumer ``read_tool_allowlist``)
        # AND grants it to this agent by name (``ToolPermissions.allowed``,
        # which bypasses the STANDARD category gate). Only with BOTH does the
        # MCP-category tool reach a STANDARD agent and execute.
        bundle = await _build_bundle(
            task_engine,
            responses=(
                make_tool_call_response(
                    tool_calls=(ToolCall(id="c1", name=_READ_TOOL, arguments={}),),
                ),
                make_text_response("There are no open tasks."),
            ),
            wiring=_Wiring(
                allowlist=(_READ_TOOL,),
                identity_allowed=(_READ_TOOL,),
            ),
        )

        result = await bundle.engine.run_chat_action(
            identity=bundle.identity,
            instruction="List the open tasks.",
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        executed = [tc for tc in result.tool_calls if tc.tool_name == _READ_TOOL]
        assert len(executed) == 1, "the operator-granted MCP tool did not run"
        assert not executed[0].is_error

    async def test_low_trust_agent_cannot_reach_admin_tool(
        self,
        task_engine: TaskEngine,
    ) -> None:
        # A STANDARD-trust agent with no operator allowlist sees NO MCP
        # surface, so an admin/destructive tool is not composed into its
        # governed invoker. Driving the instruction at it fails closed:
        # the call errors (the tool is absent) and nothing is deleted.
        bundle = await _build_bundle(
            task_engine,
            responses=(
                make_tool_call_response(
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name=_ADMIN_TOOL,
                            arguments={"confirm": True, "reason": "cleanup"},
                        ),
                    ),
                ),
                make_text_response("I could not perform that action."),
            ),
        )

        result = await bundle.engine.run_chat_action(
            identity=bundle.identity,
            instruction="Delete the marketing agent.",
        )

        assert not result.parked
        admin_calls = [tc for tc in result.tool_calls if tc.tool_name == _ADMIN_TOOL]
        assert len(admin_calls) == 1
        assert admin_calls[0].is_error, "admin tool must fail closed for low trust"


class TestSensitiveActionGatingE2E:
    """A sensitive action parks to approval and resumes via Flow 1."""

    async def test_approve_resumes_and_completes(
        self,
        task_engine: TaskEngine,
    ) -> None:
        tool = QueryTool()
        bundle = await _build_bundle(
            task_engine,
            responses=(
                make_tool_call_response(
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name="request_human_approval",
                            arguments=_APPROVAL_ARGS,
                        ),
                    ),
                ),
                # Resume turn: now authorised, the agent performs the work.
                make_tool_call_response(
                    tool_calls=(
                        ToolCall(
                            id="c2",
                            name="query_metrics",
                            arguments={"window": "release"},
                        ),
                    ),
                ),
                make_text_response("Done -- release recorded after approval."),
            ),
            wiring=_Wiring(
                extra_tools=(tool,),
                identity_allowed=("request_human_approval",),
            ),
        )

        parked = await bundle.engine.run_chat_action(
            identity=bundle.identity,
            instruction="Ship the release to production.",
        )
        assert parked.parked
        approval_id = parked.approval_id
        assert approval_id is not None
        # No side effect before consent, and the approval routes Flow 1.
        assert tool.calls == []
        item = await bundle.store.get(approval_id)
        assert item is not None
        assert item.source is ApprovalSource.PARKED_CONTEXT

        # Drive the REAL consent dispatcher: Flow 1 -> taskless resume.
        await signal_resume_intent(
            bundle.app_state,
            approval_id,
            approved=True,
            decided_by="operator-1",
        )
        await bundle.service.drain_resume_tasks()

        # The action completed under the now-authorised resume.
        assert tool.calls == [{"window": "release"}]
        # The parked context was consumed by the resume.
        assert await bundle.repo.get_by_approval(approval_id) is None

    async def test_reject_leaves_no_side_effect(
        self,
        task_engine: TaskEngine,
    ) -> None:
        tool = QueryTool()
        bundle = await _build_bundle(
            task_engine,
            responses=(
                make_tool_call_response(
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name="request_human_approval",
                            arguments=_APPROVAL_ARGS,
                        ),
                    ),
                ),
                # Resume turn after REJECT: the agent stands down.
                make_text_response("Understood, I will not proceed."),
            ),
            wiring=_Wiring(
                extra_tools=(tool,),
                identity_allowed=("request_human_approval",),
            ),
        )

        parked = await bundle.engine.run_chat_action(
            identity=bundle.identity,
            instruction="Ship the release to production.",
        )
        approval_id = parked.approval_id
        assert approval_id is not None

        await signal_resume_intent(
            bundle.app_state,
            approval_id,
            approved=False,
            decided_by="operator-1",
        )
        await bundle.service.drain_resume_tasks()

        # Rejected: the gated work never ran.
        assert tool.calls == []
