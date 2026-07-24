"""Tests for the inbound resume router."""

from dataclasses import dataclass, field

import pytest

from synthorg.integrations.chat_api.inbound.models import (
    InboundChatEvent,
    InboundEventKind,
)
from synthorg.integrations.chat_api.inbound.registry import InboundThreadRegistry
from synthorg.integrations.chat_api.inbound.router import InboundResumeRouter

pytestmark = pytest.mark.unit


@dataclass
class _FakeDispatcher:
    """Records resume calls; returns a configurable outcome."""

    outcome: bool = True
    calls: list[dict[str, object]] = field(default_factory=list)

    async def resume(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        decision_reason: str,
    ) -> bool:
        self.calls.append(
            {
                "approval_id": approval_id,
                "approved": approved,
                "decided_by": decided_by,
                "decision_reason": decision_reason,
            }
        )
        return self.outcome


def _router(
    dispatcher: _FakeDispatcher,
) -> tuple[InboundResumeRouter, InboundThreadRegistry]:
    registry = InboundThreadRegistry()
    return InboundResumeRouter(registry=registry, dispatcher=dispatcher), registry


def _reply(text: str = "go ahead", *, thread_ts: str = "100.1") -> InboundChatEvent:
    return InboundChatEvent(
        kind=InboundEventKind.MENTION,
        channel="C1",
        user="U1",
        text=text,
        thread_ts=thread_ts,
    )


def _reaction(name: str, *, thread_ts: str = "100.1") -> InboundChatEvent:
    return InboundChatEvent(
        kind=InboundEventKind.REACTION,
        channel="C1",
        user="U1",
        reaction=name,
        thread_ts=thread_ts,
    )


class TestRouting:
    async def test_untracked_thread_is_ignored(self) -> None:
        dispatcher = _FakeDispatcher()
        router, _registry = _router(dispatcher)
        await router.route(_reply())
        assert dispatcher.calls == []

    async def test_text_reply_never_approves(self) -> None:
        # Consent is explicit-token-only: arbitrary human text must never
        # be read as an approval (the Socket-Mode authz bar).
        dispatcher = _FakeDispatcher()
        router, registry = _router(dispatcher)
        registry.register(channel="C1", thread_ts="100.1", approval_id="ap-1")
        await router.route(_reply(text="looks good, proceed"))
        assert dispatcher.calls == []

    async def test_approve_reaction_reason_is_generic_not_human_text(self) -> None:
        dispatcher = _FakeDispatcher()
        router, registry = _router(dispatcher)
        registry.register(channel="C1", thread_ts="100.1", approval_id="ap-1")
        await router.route(_reaction("white_check_mark"))
        assert dispatcher.calls[0]["decision_reason"] == "Approved via reaction"

    async def test_approve_reaction_resumes_approved(self) -> None:
        dispatcher = _FakeDispatcher()
        router, registry = _router(dispatcher)
        registry.register(channel="C1", thread_ts="100.1", approval_id="ap-1")
        await router.route(_reaction("white_check_mark"))
        assert dispatcher.calls[0]["approved"] is True

    async def test_reject_reaction_resumes_rejected(self) -> None:
        dispatcher = _FakeDispatcher()
        router, registry = _router(dispatcher)
        registry.register(channel="C1", thread_ts="100.1", approval_id="ap-1")
        await router.route(_reaction("x"))
        assert dispatcher.calls[0]["approved"] is False

    async def test_unknown_reaction_is_ignored(self) -> None:
        dispatcher = _FakeDispatcher()
        router, registry = _router(dispatcher)
        registry.register(channel="C1", thread_ts="100.1", approval_id="ap-1")
        await router.route(_reaction("eyes"))
        assert dispatcher.calls == []

    async def test_empty_text_reply_is_ignored(self) -> None:
        dispatcher = _FakeDispatcher()
        router, registry = _router(dispatcher)
        registry.register(channel="C1", thread_ts="100.1", approval_id="ap-1")
        await router.route(_reply(text="   "))
        assert dispatcher.calls == []

    async def test_correlation_discarded_after_successful_resume(self) -> None:
        dispatcher = _FakeDispatcher(outcome=True)
        router, registry = _router(dispatcher)
        registry.register(channel="C1", thread_ts="100.1", approval_id="ap-1")
        await router.route(_reaction("white_check_mark"))
        # A second event on the same thread no longer resolves.
        assert registry.resolve(channel="C1", thread_ts="100.1") is None

    async def test_correlation_kept_when_resume_declined(self) -> None:
        dispatcher = _FakeDispatcher(outcome=False)
        router, registry = _router(dispatcher)
        registry.register(channel="C1", thread_ts="100.1", approval_id="ap-1")
        await router.route(_reaction("white_check_mark"))
        assert registry.resolve(channel="C1", thread_ts="100.1") == "ap-1"
