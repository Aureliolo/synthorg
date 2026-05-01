"""Unit tests for AIClient, HumanClient, and HybridClient."""

import asyncio

import pytest

from synthorg.client import (
    AIClient,
    ClientFeedback,
    ClientInterface,
    ClientProfile,
    GenerationContext,
    HumanClient,
    HybridClient,
    InMemoryHumanInputQueue,
    ReviewContext,
    TaskRequirement,
    default_router,
)

pytestmark = pytest.mark.unit


def _profile(*, client_id: str = "c1", strictness: float = 0.5) -> ClientProfile:
    return ClientProfile(
        client_id=client_id,
        name="Test",
        persona="Tester",
        strictness_level=strictness,
    )


def _gen_ctx(count: int = 1) -> GenerationContext:
    return GenerationContext(
        project_id="proj-1",
        domain="backend",
        count=count,
    )


def _rev_ctx() -> ReviewContext:
    return ReviewContext(
        task_id="task-1",
        task_title="Task",
        deliverable_summary="Deliverable summary",
    )


class _StubGenerator:
    def __init__(self, *, output: tuple[TaskRequirement, ...] = ()) -> None:
        self._output = output
        self.call_count = 0

    async def generate(self, context: GenerationContext) -> tuple[TaskRequirement, ...]:
        del context
        self.call_count += 1
        return self._output


class _StubFeedback:
    def __init__(self, *, accepted: bool = True) -> None:
        self._accepted = accepted
        self.call_count = 0

    async def evaluate(self, context: ReviewContext) -> ClientFeedback:
        self.call_count += 1
        return ClientFeedback(
            task_id=context.task_id,
            client_id="c1",
            accepted=self._accepted,
            reason=None if self._accepted else "stub rejection",
        )


class TestAIClient:
    def test_protocol_compatible(self) -> None:
        ai = AIClient(
            profile=_profile(),
            generator=_StubGenerator(),
            feedback=_StubFeedback(),
        )
        assert isinstance(ai, ClientInterface)

    async def test_submit_returns_first_requirement(self) -> None:
        req_a = TaskRequirement(title="A", description="D")
        req_b = TaskRequirement(title="B", description="D")
        ai = AIClient(
            profile=_profile(),
            generator=_StubGenerator(output=(req_a, req_b)),
            feedback=_StubFeedback(),
        )
        result = await ai.submit_requirement(_gen_ctx(count=5))
        assert result is not None
        assert result.title == "A"

    async def test_submit_returns_none_when_generator_empty(
        self,
    ) -> None:
        ai = AIClient(
            profile=_profile(),
            generator=_StubGenerator(output=()),
            feedback=_StubFeedback(),
        )
        result = await ai.submit_requirement(_gen_ctx())
        assert result is None

    async def test_submit_forces_count_of_one(self) -> None:
        generator = _StubGenerator(
            output=(TaskRequirement(title="A", description="D"),),
        )
        ai = AIClient(
            profile=_profile(),
            generator=generator,
            feedback=_StubFeedback(),
        )
        await ai.submit_requirement(_gen_ctx(count=10))
        assert generator.call_count == 1

    async def test_review_delegates_to_feedback(self) -> None:
        feedback = _StubFeedback(accepted=False)
        ai = AIClient(
            profile=_profile(),
            generator=_StubGenerator(),
            feedback=feedback,
        )
        result = await ai.review_deliverable(_rev_ctx())
        assert result.accepted is False
        assert feedback.call_count == 1


class TestHumanClient:
    def test_protocol_compatible(self) -> None:
        queue = InMemoryHumanInputQueue()
        human = HumanClient(profile=_profile(), queue=queue, timeout=60.0)
        assert isinstance(human, ClientInterface)

    def test_rejects_non_positive_timeout(self) -> None:
        queue = InMemoryHumanInputQueue()
        with pytest.raises(ValueError, match="timeout"):
            HumanClient(profile=_profile(), queue=queue, timeout=0)

    async def test_submit_waits_for_human_resolution(self) -> None:
        queue = InMemoryHumanInputQueue()
        human = HumanClient(profile=_profile(), queue=queue, timeout=1.0)

        async def resolver() -> None:
            # Give the waiter a chance to register
            await asyncio.sleep(0)
            pending = await queue.list_pending_requirements()
            assert len(pending) == 1
            await queue.resolve_requirement(
                pending[0].ticket_id,
                TaskRequirement(title="HumanReq", description="D"),
            )

        async def waiter() -> TaskRequirement | None:
            return await human.submit_requirement(_gen_ctx())

        _, result = await asyncio.gather(resolver(), waiter())
        assert result is not None
        assert result.title == "HumanReq"

    async def test_submit_returns_none_on_timeout(self) -> None:
        queue = InMemoryHumanInputQueue()
        human = HumanClient(profile=_profile(), queue=queue, timeout=0.01)
        result = await human.submit_requirement(_gen_ctx())
        assert result is None

    async def test_review_waits_for_human_resolution(self) -> None:
        queue = InMemoryHumanInputQueue()
        human = HumanClient(profile=_profile(), queue=queue, timeout=1.0)

        async def resolver() -> None:
            await asyncio.sleep(0)
            pending = await queue.list_pending_reviews()
            assert len(pending) == 1
            await queue.resolve_review(
                pending[0].ticket_id,
                ClientFeedback(
                    task_id="task-1",
                    client_id="c1",
                    accepted=True,
                ),
            )

        async def waiter() -> ClientFeedback:
            return await human.review_deliverable(_rev_ctx())

        _, feedback = await asyncio.gather(resolver(), waiter())
        assert feedback.accepted is True

    async def test_review_rejects_on_timeout(self) -> None:
        queue = InMemoryHumanInputQueue()
        human = HumanClient(profile=_profile(), queue=queue, timeout=0.01)
        feedback = await human.review_deliverable(_rev_ctx())
        assert feedback.accepted is False
        assert feedback.reason is not None
        assert "timeout" in feedback.reason.lower()


class TestHybridClient:
    def _make_hybrid(
        self, *, strictness: float
    ) -> tuple[HybridClient, _StubGenerator, _StubFeedback]:
        profile = _profile(strictness=strictness)
        ai_gen = _StubGenerator(
            output=(TaskRequirement(title="AI", description="D"),),
        )
        ai_feedback = _StubFeedback(accepted=True)
        ai = AIClient(profile=profile, generator=ai_gen, feedback=ai_feedback)
        queue = InMemoryHumanInputQueue()
        human = HumanClient(profile=profile, queue=queue, timeout=0.01)
        hybrid = HybridClient(
            profile=profile,
            ai_client=ai,
            human_client=human,
        )
        return hybrid, ai_gen, ai_feedback

    def test_protocol_compatible(self) -> None:
        hybrid, _, _ = self._make_hybrid(strictness=0.5)
        assert isinstance(hybrid, ClientInterface)

    async def test_lenient_profile_routes_to_ai(self) -> None:
        hybrid, ai_gen, _ = self._make_hybrid(strictness=0.3)
        result = await hybrid.submit_requirement(_gen_ctx())
        assert result is not None
        assert result.title == "AI"
        assert ai_gen.call_count == 1

    async def test_strict_profile_routes_to_human(self) -> None:
        hybrid, ai_gen, _ = self._make_hybrid(strictness=0.9)
        # Human delegate has 0.01s timeout so this returns None fast.
        result = await hybrid.submit_requirement(_gen_ctx())
        assert result is None
        assert ai_gen.call_count == 0

    async def test_custom_router(self) -> None:
        hybrid_default, _, _ = self._make_hybrid(strictness=0.1)
        profile = _profile(strictness=0.1)
        ai_gen = _StubGenerator(output=(TaskRequirement(title="AI", description="D"),))
        ai = AIClient(
            profile=profile,
            generator=ai_gen,
            feedback=_StubFeedback(),
        )
        queue = InMemoryHumanInputQueue()
        human = HumanClient(profile=profile, queue=queue, timeout=0.01)
        hybrid = HybridClient(
            profile=profile,
            ai_client=ai,
            human_client=human,
            router=lambda _p, _c: "human",
        )
        # Custom router forces human; default router would have used AI.
        assert default_router(profile, _gen_ctx()) == "ai"
        del hybrid_default
        result = await hybrid.submit_requirement(_gen_ctx())
        assert result is None
        assert ai_gen.call_count == 0
