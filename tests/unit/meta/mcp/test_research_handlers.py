"""Unit tests for the research-domain MCP handlers."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from synthorg.meta.mcp.handlers.research import (
    _research_get,
    _research_list,
    _research_run,
)
from synthorg.research.config import ResearchConfig
from synthorg.research.factory import build_research_service
from synthorg.research.service import ResearchService
from synthorg.tools.web.web_search import SearchResult
from tests._shared import FakeClock
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.research._fakes import (
    FakeWebSearchProvider,
    InMemoryResearchRunRepository,
    scripted_response,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)

_PLAN = json.dumps(
    {
        "research_angle": "state of the art",
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
        "summary": "An overview of the widget landscape.",
        "claims": [
            {
                "text": "Widgets are common.",
                "claim_type": "fact",
                "confidence": 0.9,
                "ref_ids": ["src-0-0"],
            }
        ],
    }
)


def _service() -> ResearchService:
    provider = ScriptedProvider(
        responses=[
            scripted_response(_PLAN),
            scripted_response(_TRIAGE),
            scripted_response(_SYNTH),
        ]
    )
    web = FakeWebSearchProvider(
        [SearchResult(title="A", url="https://a.example", snippet="widget facts")]
    )
    return build_research_service(
        runs_repo=InMemoryResearchRunRepository(),
        provider=provider,
        model="example-medium-001",
        config=ResearchConfig(enabled=True),
        web_search_provider=web,
        clock=FakeClock(start=_NOW),
    )


async def test_run_returns_cited_report() -> None:
    app_state = SimpleNamespace(
        research_service=_service(), clock=FakeClock(start=_NOW)
    )
    result = await _research_run(
        app_state=app_state,
        arguments={"question": "what are widgets?", "include_knowledge": False},
    )
    body = json.loads(result)
    assert body["status"] == "ok"
    assert body["data"]["status"] == "completed"
    assert body["data"]["report"]["claims"]


async def test_run_503_when_service_absent() -> None:
    app_state = SimpleNamespace(research_service=None)
    result = await _research_run(
        app_state=app_state,
        arguments={"question": "what are widgets?"},
    )
    body = json.loads(result)
    assert body["status"] == "error"


async def test_get_missing_returns_error() -> None:
    app_state = SimpleNamespace(research_service=_service())
    result = await _research_get(
        app_state=app_state, arguments={"run_id": "does-not-exist"}
    )
    body = json.loads(result)
    assert body["status"] == "error"


async def test_list_returns_empty_initially() -> None:
    app_state = SimpleNamespace(research_service=_service())
    result = await _research_list(app_state=app_state, arguments={})
    body = json.loads(result)
    assert body["status"] == "ok"
    assert body["data"] == []
