"""Unit tests for :mod:`synthorg.research.planning.llm_planner`."""

import json
from datetime import UTC, datetime
from typing import override

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker
from synthorg.providers.cost_recording import (
    CostRecordingContext,
    current_cost_context,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
)
from synthorg.research.enums import ResearchSourceType
from synthorg.research.errors import ResearchRunError
from synthorg.research.models import ResearchBrief
from synthorg.research.planning.llm_planner import LlmQueryPlanner
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.research._fakes import scripted_response

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)


def _brief(**overrides: object) -> ResearchBrief:
    base: dict[str, object] = {
        "brief_id": "b1",
        "title": "Mem0 survey",
        "question": "How does mem0 compare to alternatives?",
        "include_knowledge": True,
        "include_web": True,
        "created_at": _NOW,
    }
    base.update(overrides)
    return ResearchBrief(**base)  # type: ignore[arg-type]


def _plan_json(*sub_queries: dict[str, str], angle: str = "comparison") -> str:
    return json.dumps({"research_angle": angle, "sub_queries": list(sub_queries)})


async def test_planner_builds_indexed_plan() -> None:
    payload = _plan_json(
        {"source_type": "knowledge", "query_text": "mem0 internals", "intent": "core"},
        {"source_type": "web", "query_text": "mem0 vs alternatives", "intent": "cmp"},
    )
    provider = ScriptedProvider(response=scripted_response(payload, cost=0.02))
    planner = LlmQueryPlanner(provider=provider, model="example-medium-001")

    plan, cost = await planner.plan(_brief())

    assert cost == 0.02
    assert plan.brief_id == "b1"
    assert plan.research_angle == "comparison"
    assert [sq.index for sq in plan.sub_queries] == [0, 1]
    assert plan.sub_queries[0].source_type is ResearchSourceType.KNOWLEDGE


async def test_planner_drops_disabled_sources() -> None:
    payload = _plan_json(
        {"source_type": "academic", "query_text": "papers", "intent": "x"},
        {"source_type": "web", "query_text": "blogs", "intent": "y"},
    )
    provider = ScriptedProvider(response=scripted_response(payload))
    planner = LlmQueryPlanner(provider=provider, model="m")

    plan, _ = await planner.plan(_brief(include_academic=False))

    assert [sq.source_type for sq in plan.sub_queries] == [ResearchSourceType.WEB]


class _CtxCapturingProvider(ScriptedProvider):
    """ScriptedProvider that records the cost-recording context per call.

    Subclasses the full protocol impl (so typeguard accepts it) and
    captures ``current_cost_context()`` at call time to verify that
    ``complete_text`` opens the correct cost scope around the provider
    call -- research spend was previously unrecorded.
    """

    def __init__(self, payload: str) -> None:
        super().__init__(response=scripted_response(payload))
        self.captured: CostRecordingContext | None = None
        self.was_called = False

    @override
    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        self.was_called = True
        self.captured = current_cost_context()
        return await super().complete(messages, model, tools=tools, config=config)


async def test_planner_opens_system_cost_scope() -> None:
    payload = _plan_json(
        {"source_type": "web", "query_text": "q", "intent": "i"},
    )
    provider = _CtxCapturingProvider(payload)
    planner = LlmQueryPlanner(
        provider=provider,
        model="example-medium-001",
        cost_tracker=CostTracker(),
    )

    await planner.plan(_brief(project_id="proj-1"))

    assert provider.was_called
    ctx = provider.captured
    assert ctx is not None
    assert ctx.call_category is LLMCallCategory.SYSTEM
    assert ctx.task_id == "system:research:planning:b1"
    assert ctx.project_id == "proj-1"


async def test_planner_without_tracker_opens_no_scope() -> None:
    payload = _plan_json(
        {"source_type": "web", "query_text": "q", "intent": "i"},
    )
    provider = _CtxCapturingProvider(payload)
    planner = LlmQueryPlanner(
        provider=provider,
        model="example-medium-001",
    )

    await planner.plan(_brief())

    assert provider.was_called
    assert provider.captured is None


async def test_planner_caps_to_brief_budget() -> None:
    payload = _plan_json(
        *(
            {"source_type": "web", "query_text": f"q{i}", "intent": "i"}
            for i in range(5)
        )
    )
    provider = ScriptedProvider(response=scripted_response(payload))
    planner = LlmQueryPlanner(provider=provider, model="m")

    plan, _ = await planner.plan(_brief(max_subqueries=2))

    assert len(plan.sub_queries) == 2


async def test_planner_falls_back_when_no_valid_query() -> None:
    payload = _plan_json(
        {"source_type": "code", "query_text": "only disabled", "intent": "x"},
    )
    provider = ScriptedProvider(response=scripted_response(payload))
    planner = LlmQueryPlanner(provider=provider, model="m")

    plan, _ = await planner.plan(_brief(include_code=False))

    assert {sq.source_type for sq in plan.sub_queries} == {
        ResearchSourceType.KNOWLEDGE,
        ResearchSourceType.WEB,
    }


async def test_planner_raises_on_unparseable_output() -> None:
    provider = ScriptedProvider(response=scripted_response("not json at all"))
    planner = LlmQueryPlanner(provider=provider, model="m")

    with pytest.raises(ResearchRunError):
        await planner.plan(_brief())
