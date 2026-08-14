"""Tests for the two completion-gate verdict archive endpoints."""

from datetime import UTC, datetime

import pytest

from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleReport,
    CompletionOracleReportRecord,
    CompletionOracleVerdict,
)
from synthorg.security.redteam.models import (
    RedTeamReport,
    RedTeamReportRecord,
    RedTeamVerdict,
)
from tests._shared import LoopAsyncClient, sid
from tests.unit.api.conftest import make_auth_headers
from tests.unit.api.fakes_backend import FakePersistenceBackend

_RECORDED_AT = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


def _oracle_record(
    *,
    execution: str,
    reviewer: str,
    executor: str = "executor-1",
    verdict: CompletionOracleVerdict = CompletionOracleVerdict.APPROVE,
    minute: int = 0,
) -> CompletionOracleReportRecord:
    return CompletionOracleReportRecord(
        execution_id=sid(execution),
        task_id=sid("task-1"),
        verdict=verdict,
        report=CompletionOracleReport(
            execution_id=sid(execution),
            task_id=sid("task-1"),
            reviewer_agent_id=sid(reviewer),
            executor_agent_id=sid(executor),
            verdict=verdict,
            summary="reviewed",
        ),
        recorded_at=_RECORDED_AT.replace(minute=minute),
        # The record's own party columns, not the report's: the gate stamps
        # who it selected, and those are the columns the filters and the
        # per-reviewer surface read.
        reviewer_agent_id=sid(reviewer),
        executor_agent_id=sid(executor),
        reviewer_provider=sid("example-provider"),
        reviewer_model_id=sid("example-capable-001"),
        reviewer_capability="capable",
    )


def _red_team_record(
    *,
    execution: str,
    adversary: str,
    verdict: RedTeamVerdict = RedTeamVerdict.PASS,
    minute: int = 0,
) -> RedTeamReportRecord:
    return RedTeamReportRecord(
        execution_id=sid(execution),
        task_id=sid("task-1"),
        verdict=verdict,
        report=RedTeamReport(
            execution_id=sid(execution),
            task_id=sid("task-1"),
            summary="attacked",
        ),
        recorded_at=_RECORDED_AT.replace(minute=minute),
        red_team_agent_id=sid(adversary),
        executor_agent_id=sid("executor-1"),
        red_team_provider=sid("example-provider"),
        red_team_model_id=sid("example-expert-001"),
        red_team_capability="expert",
    )


class TestCompletionOracleReports:
    """``GET /completion-oracle/reports``."""

    @pytest.mark.unit
    async def test_a_verdict_names_the_reviewer_and_the_model_it_ran(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.completion_oracle_reports.append(
            _oracle_record(execution="exec-1", reviewer="reviewer-a")
        )
        resp = await async_test_client.get(
            "/api/v1/completion-oracle/reports",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        assert row["report"]["reviewer_agent_id"] == sid("reviewer-a")
        assert row["reviewer_provider"] == sid("example-provider")
        assert row["reviewer_model_id"] == sid("example-capable-001")
        assert row["reviewer_capability"] == "capable"

    @pytest.mark.unit
    async def test_filtering_by_reviewer_is_what_makes_verdicts_comparable(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.completion_oracle_reports.append(
            _oracle_record(execution="exec-1", reviewer="reviewer-a")
        )
        await fake_persistence.completion_oracle_reports.append(
            _oracle_record(execution="exec-2", reviewer="reviewer-b", minute=1)
        )
        resp = await async_test_client.get(
            "/api/v1/completion-oracle/reports",
            params={"reviewer_agent_id": sid("reviewer-a")},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [r["execution_id"] for r in body["data"]] == [sid("exec-1")]
        assert body["pagination"]["has_more"] is False

    @pytest.mark.unit
    async def test_a_re_reviewed_execution_returns_both_verdicts_newest_first(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.completion_oracle_reports.append(
            _oracle_record(
                execution="exec-1",
                reviewer="reviewer-a",
                verdict=CompletionOracleVerdict.REJECT,
            )
        )
        await fake_persistence.completion_oracle_reports.append(
            _oracle_record(execution="exec-1", reviewer="reviewer-a", minute=5)
        )
        resp = await async_test_client.get(
            "/api/v1/completion-oracle/reports",
            params={"execution_id": sid("exec-1")},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        verdicts = [r["verdict"] for r in resp.json()["data"]]
        assert verdicts == ["approve", "reject"]

    @pytest.mark.unit
    async def test_filtering_by_verdict(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.completion_oracle_reports.append(
            _oracle_record(execution="exec-1", reviewer="reviewer-a")
        )
        await fake_persistence.completion_oracle_reports.append(
            _oracle_record(
                execution="exec-2",
                reviewer="reviewer-a",
                verdict=CompletionOracleVerdict.ESCALATE,
                minute=1,
            )
        )
        resp = await async_test_client.get(
            "/api/v1/completion-oracle/reports",
            params={"verdict": "escalate"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert [r["execution_id"] for r in resp.json()["data"]] == [sid("exec-2")]

    @pytest.mark.unit
    async def test_a_page_carries_a_cursor_when_more_remain(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        for i in range(3):
            await fake_persistence.completion_oracle_reports.append(
                _oracle_record(execution=f"exec-{i}", reviewer="reviewer-a", minute=i)
            )
        resp = await async_test_client.get(
            "/api/v1/completion-oracle/reports",
            params={"limit": 2},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["has_more"] is True
        assert body["pagination"]["next_cursor"]

    @pytest.mark.unit
    async def test_the_cursor_resumes_past_a_verdict_written_mid_walk(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """A verdict landing between two page reads shifts nothing.

        The gates write to this archive while an operator reads it, so an
        offset cursor would push a row the caller has already seen onto the
        next page and drop another off the end.
        """
        for i in range(4):
            await fake_persistence.completion_oracle_reports.append(
                _oracle_record(execution=f"exec-{i}", reviewer="reviewer-a", minute=i)
            )
        first = await async_test_client.get(
            "/api/v1/completion-oracle/reports",
            params={"limit": 2},
            headers=make_auth_headers("ceo"),
        )
        assert first.status_code == 200
        first_body = first.json()
        assert [r["execution_id"] for r in first_body["data"]] == [
            sid("exec-3"),
            sid("exec-2"),
        ]

        await fake_persistence.completion_oracle_reports.append(
            _oracle_record(execution="late", reviewer="reviewer-a", minute=30)
        )
        second = await async_test_client.get(
            "/api/v1/completion-oracle/reports",
            params={"limit": 2, "cursor": first_body["pagination"]["next_cursor"]},
            headers=make_auth_headers("ceo"),
        )

        assert second.status_code == 200
        assert [r["execution_id"] for r in second.json()["data"]] == [
            sid("exec-1"),
            sid("exec-0"),
        ]

    @pytest.mark.unit
    async def test_an_empty_archive_is_an_empty_page(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/completion-oracle/reports",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.unit
    async def test_the_summary_counts_every_verdict_kind_not_one_page(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        for i in range(3):
            await fake_persistence.completion_oracle_reports.append(
                _oracle_record(execution=f"ok-{i}", reviewer="reviewer-a", minute=i)
            )
        await fake_persistence.completion_oracle_reports.append(
            _oracle_record(
                execution="bad",
                reviewer="reviewer-a",
                verdict=CompletionOracleVerdict.REJECT,
                minute=4,
            )
        )
        await fake_persistence.completion_oracle_reports.append(
            _oracle_record(execution="other", reviewer="reviewer-b", minute=5)
        )
        resp = await async_test_client.get(
            "/api/v1/completion-oracle/reports/summary",
            params={"reviewer_agent_id": sid("reviewer-a")},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        summary = resp.json()["data"]
        assert summary["total"] == 4
        assert summary["by_verdict"]["approve"] == 3
        assert summary["by_verdict"]["reject"] == 1
        assert summary["by_verdict"]["escalate"] == 0


class TestRedTeamReports:
    """``GET /red-team/reports``."""

    @pytest.mark.unit
    async def test_a_verdict_names_the_adversary_and_the_model_it_ran(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.red_team_reports.append(
            _red_team_record(execution="exec-1", adversary="adversary-a")
        )
        resp = await async_test_client.get(
            "/api/v1/red-team/reports",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        assert row["red_team_agent_id"] == sid("adversary-a")
        assert row["executor_agent_id"] == sid("executor-1")
        assert row["red_team_model_id"] == sid("example-expert-001")
        assert row["red_team_capability"] == "expert"

    @pytest.mark.unit
    async def test_filtering_by_adversary(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.red_team_reports.append(
            _red_team_record(execution="exec-1", adversary="adversary-a")
        )
        await fake_persistence.red_team_reports.append(
            _red_team_record(execution="exec-2", adversary="adversary-b", minute=1)
        )
        resp = await async_test_client.get(
            "/api/v1/red-team/reports",
            params={"red_team_agent_id": sid("adversary-b")},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert [r["execution_id"] for r in resp.json()["data"]] == [sid("exec-2")]

    @pytest.mark.unit
    async def test_filtering_by_verdict(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.red_team_reports.append(
            _red_team_record(execution="exec-1", adversary="adversary-a")
        )
        await fake_persistence.red_team_reports.append(
            _red_team_record(
                execution="exec-2",
                adversary="adversary-a",
                verdict=RedTeamVerdict.BLOCK,
                minute=1,
            )
        )
        resp = await async_test_client.get(
            "/api/v1/red-team/reports",
            params={"verdict": "block"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert [r["execution_id"] for r in resp.json()["data"]] == [sid("exec-2")]

    @pytest.mark.unit
    async def test_the_summary_counts_every_verdict_kind(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.red_team_reports.append(
            _red_team_record(execution="exec-1", adversary="adversary-a")
        )
        await fake_persistence.red_team_reports.append(
            _red_team_record(
                execution="exec-2",
                adversary="adversary-a",
                verdict=RedTeamVerdict.BLOCK,
                minute=1,
            )
        )
        resp = await async_test_client.get(
            "/api/v1/red-team/reports/summary",
            params={"red_team_agent_id": sid("adversary-a")},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        summary = resp.json()["data"]
        assert summary["total"] == 2
        assert summary["by_verdict"]["pass"] == 1
        assert summary["by_verdict"]["block"] == 1
        assert summary["by_verdict"]["pass_with_findings"] == 0
