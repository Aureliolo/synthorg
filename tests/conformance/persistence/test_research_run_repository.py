"""Conformance tests for ``ResearchRunRepository`` (SQLite + Postgres).

Asserts the shared contract: id save/get/upsert, recency-first list
ordering, brief / project / status filters, count parity with query, the
full JSON round-trip of a completed run (brief snapshot, plan, items,
verdicts, cited report), delete, and FK cascade from ``projects`` for
project-scoped runs (global runs survive).
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.enums import (
    ClaimType,
    ResearchRunStatus,
    ResearchSourceType,
)
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.research_protocol import ResearchRunFilter
from synthorg.research.models import (
    ResearchBrief,
    ResearchCitation,
    ResearchClaim,
    ResearchQueryPlan,
    ResearchReport,
    ResearchRun,
    RetrievedItem,
    SourceCredibility,
    SubQuery,
    WebSourceLocator,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 22, tzinfo=UTC)
_HASH = "a" * 64


def _project(project_id: str = "proj-1") -> Project:
    return Project(id=NotBlankStr(project_id), name=NotBlankStr("Demo"))


def _brief(*, brief_id: str = "b1", project_id: str | None = "proj-1") -> ResearchBrief:
    return ResearchBrief(
        brief_id=NotBlankStr(brief_id),
        project_id=NotBlankStr(project_id) if project_id is not None else None,
        title="Widget research",
        question="what is the state of widgets?",
        created_at=_NOW,
    )


def _run(
    *,
    run_id: str = "run-1",
    brief_id: str = "b1",
    project_id: str | None = "proj-1",
    status: ResearchRunStatus = ResearchRunStatus.PLANNING,
    ts: datetime | None = None,
) -> ResearchRun:
    return ResearchRun(
        run_id=NotBlankStr(run_id),
        brief_id=NotBlankStr(brief_id),
        project_id=NotBlankStr(project_id) if project_id is not None else None,
        status=status,
        brief=_brief(brief_id=brief_id, project_id=project_id),
        created_by=NotBlankStr("agent-1"),
        created_at=ts if ts is not None else _NOW,
    )


def _completed_run() -> ResearchRun:
    citation = ResearchCitation(
        ref_id=NotBlankStr("src-0-0"),
        source_type=ResearchSourceType.WEB,
        external=WebSourceLocator(
            url=NotBlankStr("https://x.example"), accessed_at=_NOW
        ),
    )
    item = RetrievedItem(
        ref_id=NotBlankStr("src-0-0"),
        sub_query_index=0,
        source_type=ResearchSourceType.WEB,
        title="A study",
        uri=NotBlankStr("https://x.example"),
        snippet="evidence",
        content_hash=_HASH,
        relevance_score=0.8,
        citation=citation,
    )
    report = ResearchReport(
        report_id=NotBlankStr("report-b1"),
        brief_id=NotBlankStr("b1"),
        title="Widgets",
        summary="Summary of the widget landscape.",
        claims=(
            ResearchClaim(
                claim_id=NotBlankStr("claim-0"),
                text="Widgets are adopted.",
                claim_type=ClaimType.FACT,
                citations=(citation,),
                confidence=0.9,
            ),
        ),
        sources_consulted=1,
        sources_retained=1,
        research_angle="adoption",
        synthesis_model=NotBlankStr("example-medium-001"),
        created_at=_NOW,
    )
    return _run().model_copy(
        update={
            "status": ResearchRunStatus.COMPLETED,
            "query_plan": ResearchQueryPlan(
                brief_id=NotBlankStr("b1"),
                research_angle="adoption",
                sub_queries=(
                    SubQuery(
                        index=0,
                        source_type=ResearchSourceType.WEB,
                        query_text="widgets",
                        intent="probe",
                    ),
                ),
            ),
            "retrieved_items": (item,),
            "credibility": (
                SourceCredibility(
                    ref_id=NotBlankStr("src-0-0"),
                    score=0.8,
                    authority="community",
                    domain_alignment=0.7,
                    passed=True,
                ),
            ),
            "report": report,
            "cost": 0.12,
            "wall_clock_seconds": 1.5,
            "completed_at": _NOW,
        }
    )


class TestResearchRunRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.research_runs.save(_run())
        fetched = await backend.research_runs.get(NotBlankStr("run-1"))
        assert fetched is not None
        assert fetched.status is ResearchRunStatus.PLANNING
        assert fetched.brief.question == "what is the state of widgets?"

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.research_runs.get(NotBlankStr("ghost")) is None

    async def test_completed_run_round_trips(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        run = _completed_run()
        await backend.research_runs.save(run)
        fetched = await backend.research_runs.get(NotBlankStr("run-1"))
        assert fetched == run

    async def test_upsert_updates_in_place(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.research_runs.save(_run())
        await backend.research_runs.save(
            _run().model_copy(
                update={
                    "status": ResearchRunStatus.FAILED,
                    "error": NotBlankStr("x"),
                    "completed_at": _NOW,
                }
            )
        )
        fetched = await backend.research_runs.get(NotBlankStr("run-1"))
        assert fetched is not None
        assert fetched.status is ResearchRunStatus.FAILED
        assert await backend.research_runs.count(ResearchRunFilter()) == 1

    async def test_global_run_round_trip(self, backend: PersistenceBackend) -> None:
        await backend.research_runs.save(
            _run(run_id="glob", brief_id="bg", project_id=None)
        )
        fetched = await backend.research_runs.get(NotBlankStr("glob"))
        assert fetched is not None
        assert fetched.project_id is None

    async def test_list_orders_recent_first(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.research_runs.save(
            _run(run_id="old", ts=_NOW - timedelta(hours=1))
        )
        await backend.research_runs.save(_run(run_id="new", ts=_NOW))
        runs = await backend.research_runs.list_items()
        ids = [r.run_id for r in runs]
        assert ids.index("new") < ids.index("old")

    async def test_query_filters(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.projects.save(_project("proj-2"))
        await backend.research_runs.save(_run(run_id="r-a", brief_id="b1"))
        await backend.research_runs.save(
            _run(run_id="r-b", brief_id="b2", project_id="proj-2")
        )
        by_brief = await backend.research_runs.query(ResearchRunFilter(brief_id="b1"))
        assert {r.run_id for r in by_brief} == {"r-a"}
        by_project = await backend.research_runs.query(
            ResearchRunFilter(project_id="proj-2")
        )
        assert {r.run_id for r in by_project} == {"r-b"}
        by_status = await backend.research_runs.query(
            ResearchRunFilter(status=ResearchRunStatus.PLANNING)
        )
        assert len(by_status) == 2

    async def test_delete(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.research_runs.save(_run())
        assert await backend.research_runs.delete(NotBlankStr("run-1")) is True
        assert await backend.research_runs.delete(NotBlankStr("run-1")) is False

    async def test_project_cascade_deletes_scoped_runs(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        await backend.research_runs.save(_run(run_id="scoped"))
        await backend.research_runs.save(
            _run(run_id="glob", brief_id="bg", project_id=None)
        )
        await backend.projects.delete(NotBlankStr("proj-1"))
        assert await backend.research_runs.get(NotBlankStr("scoped")) is None
        assert await backend.research_runs.get(NotBlankStr("glob")) is not None
