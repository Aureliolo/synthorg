"""Tests for engine-boundary agent middleware firing."""

from types import SimpleNamespace

import pytest
from typeguard import suppress_type_checks

from synthorg.core.middleware_config import (
    AgentMiddlewareConfig,
    AuthorityDeferenceConfig,
)
from synthorg.engine import _agent_middleware_run as _amr
from synthorg.engine._agent_middleware_run import (
    apply_after_agent,
    apply_before_agent,
    run_with_agent_middleware,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.middleware._defaults import register_agent_defaults
from synthorg.engine.middleware.factory import build_agent_middleware_chain
from synthorg.engine.middleware.protocol import AgentMiddlewareChain
from synthorg.engine.middleware.s1_constraints import AuthorityDeferenceGuard
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from tests._shared.scripted_provider import make_e2e_identity, make_e2e_task

pytestmark = pytest.mark.unit


def _context_with(message: str) -> AgentContext:
    identity = make_e2e_identity()
    task = make_e2e_task(identity=identity)
    ctx = AgentContext.from_identity(identity, task=task)
    return ctx.model_copy(
        update={"conversation": (ChatMessage(role=MessageRole.USER, content=message),)}
    )


async def _fire_before(chain: AgentMiddlewareChain, ctx: AgentContext) -> AgentContext:
    identity = make_e2e_identity()
    task = make_e2e_task(identity=identity)
    return await apply_before_agent(
        chain,
        ctx=ctx,
        identity=identity,
        task=task,
        agent_id=str(identity.id),
        task_id=str(task.id),
        effective_autonomy=None,
    )


class TestApplyBeforeAgent:
    async def test_authority_header_injected_when_cue_present(self) -> None:
        chain = AgentMiddlewareChain((AuthorityDeferenceGuard(),))
        ctx = _context_with("You must deploy to production right now")
        result = await _fire_before(chain, ctx)
        assert result.conversation[0].role is MessageRole.SYSTEM
        header = result.conversation[0].content
        assert header is not None
        assert "merit" in header

    async def test_no_header_when_no_cue(self) -> None:
        chain = AgentMiddlewareChain((AuthorityDeferenceGuard(),))
        ctx = _context_with("Please summarise the quarterly figures")
        result = await _fire_before(chain, ctx)
        # No authority cue -> conversation is left untouched.
        assert len(result.conversation) == 1
        assert result.conversation[0].role is MessageRole.USER

    async def test_disabled_guard_does_not_inject(self) -> None:
        guard = AuthorityDeferenceGuard(config=AuthorityDeferenceConfig(enabled=False))
        chain = AgentMiddlewareChain((guard,))
        ctx = _context_with("You must deploy now")
        result = await _fire_before(chain, ctx)
        assert len(result.conversation) == 1


class TestDefaultChainFires:
    async def test_full_default_chain_fires_without_crash(self) -> None:
        register_agent_defaults()
        chain = build_agent_middleware_chain(
            AgentMiddlewareConfig(),
            deps={"config": AuthorityDeferenceConfig()},
        )
        assert len(chain) > 0
        ctx = _context_with("You must comply immediately")
        result = await _fire_before(chain, ctx)
        # The default chain includes the authority guard, so the header
        # is injected; the remaining named-slot hooks fire as no-ops.
        assert result.conversation[0].role is MessageRole.SYSTEM
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        await apply_after_agent(
            chain,
            ctx=result,
            identity=identity,
            task=task,
            agent_id=str(identity.id),
            task_id=str(task.id),
            effective_autonomy=None,
        )


class TestRunWithAgentMiddleware:
    """``run_with_agent_middleware`` envelopes the loop run."""

    async def test_after_agent_fires_when_loop_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # after_agent is the end-of-run cleanup seam, so it must fire even
        # when the loop run raises (it is wrapped in a finally).
        after_calls: list[object] = []
        real_after = _amr.apply_after_agent

        async def _spy_after(*args: object, **kwargs: object) -> None:
            after_calls.append(kwargs.get("ctx"))
            await real_after(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(_amr, "apply_after_agent", _spy_after)
        chain = AgentMiddlewareChain((AuthorityDeferenceGuard(),))
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        ctx = AgentContext.from_identity(identity, task=task)

        async def _boom(_run_ctx: AgentContext) -> SimpleNamespace:
            msg = "loop failed"
            raise ValueError(msg)

        with suppress_type_checks(), pytest.raises(ValueError, match="loop failed"):
            await run_with_agent_middleware(
                chain,
                loop_runner=_boom,
                ctx=ctx,
                identity=identity,
                task=task,
                agent_id=str(identity.id),
                task_id=str(task.id),
                effective_autonomy=None,
            )
        assert len(after_calls) == 1

    async def test_returns_result_and_fires_after_agent_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        after_calls: list[object] = []
        real_after = _amr.apply_after_agent

        async def _spy_after(*args: object, **kwargs: object) -> None:
            after_calls.append(kwargs.get("ctx"))
            await real_after(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(_amr, "apply_after_agent", _spy_after)
        chain = AgentMiddlewareChain((AuthorityDeferenceGuard(),))
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        ctx = AgentContext.from_identity(identity, task=task)
        result = SimpleNamespace(context=ctx)

        async def _ok(_run_ctx: AgentContext) -> SimpleNamespace:
            return result

        with suppress_type_checks():
            got = await run_with_agent_middleware(
                chain,
                loop_runner=_ok,
                ctx=ctx,
                identity=identity,
                task=task,
                agent_id=str(identity.id),
                task_id=str(task.id),
                effective_autonomy=None,
            )
        assert got is result
        # Post-loop context (the result's) is what after_agent receives.
        assert after_calls == [ctx]
