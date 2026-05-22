"""Unit tests for the agent-facing :class:`ResearchTool`."""

import json
from datetime import UTC, datetime

import pytest
from tests._shared import FakeClock
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.research._fakes import (
    FakeWebSearchProvider,
    InMemoryResearchRunRepository,
    scripted_response,
)

from synthorg.core.types import NotBlankStr
from synthorg.research.config import ResearchConfig
from synthorg.research.factory import build_research_service
from synthorg.research.tool import (
    ResearchBriefArgs,
    ResearchTool,
    derive_research_ids,
)
from synthorg.tools.web.web_search import SearchResult

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)

_PLAN = json.dumps(
    {
        "research_angle": "x",
        "sub_queries": [
            {"source_type": "web", "query_text": "widgets", "intent": "core"}
        ],
    }
)
_TRIAGE = json.dumps(
    {
        "verdicts": [
            {
                "ref_id": "src-0-0",
                "authority": "community",
                "domain_alignment": 0.8,
                "score": 0.9,
                "red_flags": [],
            }
        ]
    }
)
_SYNTH = json.dumps(
    {
        "title": "Widgets",
        "summary": "Overview of widgets.",
        "claims": [
            {
                "text": "Widgets exist.",
                "claim_type": "fact",
                "confidence": 0.9,
                "ref_ids": ["src-0-0"],
            }
        ],
    }
)


def _tool(provider: ScriptedProvider) -> ResearchTool:
    web = FakeWebSearchProvider(
        [SearchResult(title="A", url="https://a.example", snippet="widget facts")]
    )
    service = build_research_service(
        runs_repo=InMemoryResearchRunRepository(),
        provider=provider,
        model="example-medium-001",
        config=ResearchConfig(enabled=True),
        web_search_provider=web,
        clock=FakeClock(start=_NOW),
    )
    return ResearchTool(
        service=service,
        project_id=None,
        created_by=NotBlankStr("agent-1"),
        clock=FakeClock(start=_NOW),
    )


async def test_execute_returns_rendered_cited_report() -> None:
    provider = ScriptedProvider(
        responses=[
            scripted_response(_PLAN),
            scripted_response(_TRIAGE),
            scripted_response(_SYNTH),
        ]
    )
    result = await _tool(provider).execute(
        arguments={"question": "what are widgets?", "include_knowledge": False}
    )
    assert result.is_error is False
    assert "Widgets exist." in result.content
    assert result.metadata["claim_count"] == 1
    assert str(result.metadata["run_id"]).startswith("run-")


async def test_execute_returns_error_on_pipeline_failure() -> None:
    provider = ScriptedProvider(response=scripted_response("not json"))
    result = await _tool(provider).execute(
        arguments={"question": "what are widgets?", "include_knowledge": False}
    )
    assert result.is_error is True


def test_derive_ids_are_deterministic() -> None:
    args = ResearchBriefArgs(question="what are widgets?")
    first = derive_research_ids(args, project_id=None)
    second = derive_research_ids(args, project_id=None)
    assert first == second
    assert first[0].startswith("brief-")
    assert first[1].startswith("run-")


def test_derive_ids_differ_by_project() -> None:
    args = ResearchBriefArgs(question="what are widgets?")
    assert derive_research_ids(args, project_id=None) != derive_research_ids(
        args, project_id=NotBlankStr("proj-1")
    )
