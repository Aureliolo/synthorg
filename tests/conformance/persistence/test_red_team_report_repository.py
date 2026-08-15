"""Conformance tests for ``RedTeamReportArchiveRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.red_team_report_protocol import RedTeamReportFilterSpec
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamReport,
    RedTeamReportRecord,
    RedTeamSeverity,
    RedTeamVerdict,
)

pytestmark = pytest.mark.integration


class _Adversary(NamedTuple):
    """Who attacked, whose work it was, and what the adversary ran on.

    One value rather than five keywords: the five travel together on every
    row, and an adversary named without the pair it dispatched on is exactly
    the half-attribution these columns exist to prevent. Every field defaults
    to ``None`` because a row written before the columns existed knows none
    of them, and that case is under test.
    """

    agent_id: str | None = None
    executor_id: str | None = None
    provider: str | None = None
    model_id: str | None = None
    capability: CapabilityLevel | None = None


_UNATTRIBUTED = _Adversary()


def _optional(value: str | None) -> NotBlankStr | None:
    """Return the non-blank form of an optional column value.

    Returns:
        The wrapped value, or ``None``.
    """
    return None if value is None else NotBlankStr(value)


def _record(
    *,
    execution_id: str = "exec-001",
    task_id: str = "task-001",
    verdict: RedTeamVerdict = RedTeamVerdict.BLOCK,
    findings: tuple[RedTeamFinding, ...] | None = None,
    summary: str = "Adversarial review complete.",
    recorded_at: datetime | None = None,
    adversary: _Adversary = _UNATTRIBUTED,
) -> RedTeamReportRecord:
    default_findings = (
        RedTeamFinding(
            attack_surface=RedTeamAttackSurface.SECURITY,
            severity=RedTeamSeverity.HIGH,
            description="hardcoded credential in source",
            evidence=("api_key = 'sk-live-123'",),
            suggested_fix="Load the credential from a secret backend.",
        ),
    )
    report = RedTeamReport(
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr(task_id),
        findings=default_findings if findings is None else findings,
        summary=NotBlankStr(summary),
    )
    return RedTeamReportRecord(
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr(task_id),
        verdict=verdict,
        report=report,
        recorded_at=recorded_at or datetime.now(UTC),
        red_team_agent_id=_optional(adversary.agent_id),
        executor_agent_id=_optional(adversary.executor_id),
        red_team_provider=_optional(adversary.provider),
        red_team_model_id=_optional(adversary.model_id),
        red_team_capability=adversary.capability,
    )


class TestRedTeamReportArchiveRepository:
    async def test_append_and_query_by_execution(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.red_team_reports.append(_record())

        page = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert len(page) == 1
        record = page[0]
        assert record.execution_id == "exec-001"
        assert record.task_id == "task-001"
        assert record.verdict is RedTeamVerdict.BLOCK
        # The full merged report round-trips through ``report_json``.
        assert len(record.report.findings) == 1
        finding = record.report.findings[0]
        assert finding.attack_surface is RedTeamAttackSurface.SECURITY
        assert finding.severity is RedTeamSeverity.HIGH
        assert finding.evidence == ("api_key = 'sk-live-123'",)

    async def test_a_re_attacked_execution_keeps_both_reports(
        self, backend: PersistenceBackend
    ) -> None:
        """A row is one attack event, not one execution.

        The gate runs again whenever a task is decided, re-opened and decided
        again, against the same recorded frame and so the same execution id.
        Keyed on that id alone, the second report collided and was swallowed
        fail-open, leaving the verdict that actually stood with no evidence
        behind it while a superseded row remained the durable record.
        """
        # One timestamp for both, deliberately: a re-attack follows a human
        # re-opening the task, not a clock, and every column the sort was
        # keyed on before the archive key existed is one these two share.
        attacked_at = datetime.now(UTC)
        await backend.red_team_reports.append(
            _record(
                execution_id="dup",
                verdict=RedTeamVerdict.BLOCK,
                recorded_at=attacked_at,
            ),
        )
        await backend.red_team_reports.append(
            _record(
                execution_id="dup",
                verdict=RedTeamVerdict.PASS,
                findings=(),
                summary="No findings on the reworked deliverable.",
                recorded_at=attacked_at,
            ),
        )

        page = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(execution_id=NotBlankStr("dup")),
        )

        assert [record.verdict for record in page] == [
            RedTeamVerdict.PASS,
            RedTeamVerdict.BLOCK,
        ]

    async def test_the_adversary_and_the_model_it_ran_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        """The gate records who attacked, on what, and whose work it was."""
        await backend.red_team_reports.append(
            _record(
                execution_id="bound",
                adversary=_Adversary(
                    agent_id="adversary-a",
                    executor_id="executor-1",
                    provider="example-provider",
                    model_id="example-expert-001",
                    capability="expert",
                ),
            ),
        )
        page = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(execution_id=NotBlankStr("bound")),
        )
        assert page[0].red_team_agent_id == "adversary-a"
        assert page[0].executor_agent_id == "executor-1"
        assert page[0].red_team_provider == "example-provider"
        assert page[0].red_team_model_id == "example-expert-001"
        assert page[0].red_team_capability == "expert"

    async def test_a_row_written_before_the_columns_existed_reads_as_unknown(
        self, backend: PersistenceBackend
    ) -> None:
        """NULL is the honest value, never a fabricated attribution."""
        await backend.red_team_reports.append(_record(execution_id="legacy"))
        page = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(execution_id=NotBlankStr("legacy")),
        )
        assert page[0].red_team_agent_id is None
        assert page[0].executor_agent_id is None
        assert page[0].red_team_model_id is None
        assert page[0].red_team_capability is None

    async def test_query_filters_by_adversary(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.red_team_reports.append(
            _record(execution_id="e1", adversary=_Adversary(agent_id="adversary-a")),
        )
        await backend.red_team_reports.append(
            _record(execution_id="e2", adversary=_Adversary(agent_id="adversary-b")),
        )
        page = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(red_team_agent_id=NotBlankStr("adversary-b")),
        )
        assert {r.execution_id for r in page} == {"e2"}

    async def test_count_agrees_with_query_under_the_same_filter(
        self, backend: PersistenceBackend
    ) -> None:
        """A count derived from one page would report a window as a total."""
        for i in range(3):
            await backend.red_team_reports.append(
                _record(
                    execution_id=f"e{i}", adversary=_Adversary(agent_id="adversary-a")
                ),
            )
        await backend.red_team_reports.append(
            _record(execution_id="other", adversary=_Adversary(agent_id="adversary-b")),
        )
        spec = RedTeamReportFilterSpec(red_team_agent_id=NotBlankStr("adversary-a"))
        assert await backend.red_team_reports.count(spec) == 3
        assert len(await backend.red_team_reports.query(spec, limit=2)) == 2
        assert await backend.red_team_reports.count(RedTeamReportFilterSpec()) == 4

    async def test_the_table_refuses_a_self_attack(
        self, backend: PersistenceBackend
    ) -> None:
        """The red-team gate gains the structural guard its twin always had.

        The record validator refuses a self-attack first, so the record is
        built past it deliberately: what is under test is the layer that
        still holds when something upstream lies.
        """
        report = RedTeamReport(
            execution_id=NotBlankStr("self"),
            task_id=NotBlankStr("task-001"),
            findings=(),
            summary=NotBlankStr("Attacked my own work."),
        )
        record = RedTeamReportRecord.model_construct(
            execution_id=NotBlankStr("self"),
            task_id=NotBlankStr("task-001"),
            verdict=RedTeamVerdict.PASS,
            report=report,
            recorded_at=datetime.now(UTC),
            red_team_agent_id=NotBlankStr("same-agent"),
            executor_agent_id=NotBlankStr("same-agent"),
            red_team_provider=None,
            red_team_model_id=None,
            red_team_capability=None,
        )
        with pytest.raises(QueryError):
            await backend.red_team_reports.append(record)

    async def test_query_newest_first_by_recorded_at(
        self, backend: PersistenceBackend
    ) -> None:
        base = datetime.now(UTC)
        await backend.red_team_reports.append(
            _record(execution_id="old", recorded_at=base - timedelta(hours=1)),
        )
        await backend.red_team_reports.append(
            _record(execution_id="new", recorded_at=base),
        )
        page = await backend.red_team_reports.query(RedTeamReportFilterSpec())
        assert [r.execution_id for r in page] == ["new", "old"]

    async def test_query_filters_by_task_and_verdict(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.red_team_reports.append(
            _record(execution_id="e1", task_id="t1", verdict=RedTeamVerdict.BLOCK),
        )
        await backend.red_team_reports.append(
            _record(
                execution_id="e2",
                task_id="t2",
                verdict=RedTeamVerdict.PASS,
                findings=(),
                summary="No findings.",
            ),
        )
        by_task = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(task_id=NotBlankStr("t1")),
        )
        assert {r.execution_id for r in by_task} == {"e1"}
        blocked = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(verdict=RedTeamVerdict.BLOCK),
        )
        assert {r.execution_id for r in blocked} == {"e1"}

    async def test_query_pagination(self, backend: PersistenceBackend) -> None:
        base = datetime.now(UTC)
        for index in range(5):
            await backend.red_team_reports.append(
                _record(
                    execution_id=f"e{index}",
                    recorded_at=base - timedelta(minutes=index),
                ),
            )
        first = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(), limit=2, offset=0
        )
        second = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(), limit=2, offset=2
        )
        assert [r.execution_id for r in first] == ["e0", "e1"]
        assert [r.execution_id for r in second] == ["e2", "e3"]

    async def test_the_store_assigns_the_archive_key(
        self, backend: PersistenceBackend
    ) -> None:
        """Every row read back names its own position in the archive.

        The keyset cursor is built from it, so a backend that left it unset
        would page from a position naming nothing.
        """
        attacked_at = datetime.now(UTC)
        await backend.red_team_reports.append(
            _record(execution_id="k1", recorded_at=attacked_at),
        )
        await backend.red_team_reports.append(
            _record(execution_id="k2", recorded_at=attacked_at),
        )

        page = await backend.red_team_reports.query(RedTeamReportFilterSpec())

        keys = [r.report_id for r in page]
        assert all(k is not None for k in keys)
        assert len(set(keys)) == len(keys)

    async def test_keyset_paging_is_stable_across_a_concurrent_write(
        self, backend: PersistenceBackend
    ) -> None:
        """A verdict landing mid-walk shifts nothing already paged.

        This is the whole reason the cursor is a position rather than an
        offset: an archive is written to while it is read, and an offset
        would show the caller a row it has already seen.
        """
        base = datetime.now(UTC)
        for index in range(4):
            await backend.red_team_reports.append(
                _record(
                    execution_id=f"e{index}",
                    recorded_at=base - timedelta(minutes=index),
                ),
            )
        first = await backend.red_team_reports.query(RedTeamReportFilterSpec(), limit=2)
        assert [r.execution_id for r in first] == ["e0", "e1"]

        # A newer verdict arrives between the two page reads. Under an offset
        # it would push ``e1`` into the second page and show it twice.
        await backend.red_team_reports.append(
            _record(execution_id="late", recorded_at=base + timedelta(minutes=1)),
        )
        boundary = first[-1]
        assert boundary.report_id is not None
        second = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(
                after_recorded_at=boundary.recorded_at,
                after_report_id=boundary.report_id,
            ),
            limit=2,
        )

        assert [r.execution_id for r in second] == ["e2", "e3"]

    async def test_the_cursor_keeps_rows_sharing_an_instant_apart(
        self, backend: PersistenceBackend
    ) -> None:
        """Two attacks recorded at one instant page one at a time.

        A cursor comparing the timestamp alone would drop both rows at the
        boundary, which is exactly the case the surrogate key exists for.
        """
        attacked_at = datetime.now(UTC)
        for index in range(3):
            await backend.red_team_reports.append(
                _record(execution_id=f"same-{index}", recorded_at=attacked_at),
            )
        first = await backend.red_team_reports.query(RedTeamReportFilterSpec(), limit=1)
        boundary = first[0]
        assert boundary.report_id is not None

        rest = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(
                after_recorded_at=boundary.recorded_at,
                after_report_id=boundary.report_id,
            ),
        )

        assert len(rest) == 2
        assert boundary.execution_id not in {r.execution_id for r in rest}

    async def test_a_count_ignores_the_paging_cursor(
        self, backend: PersistenceBackend
    ) -> None:
        """The total answers the filter, never the caller's position in it.

        One spec legitimately serves both the page and the total, so a count
        that honoured the cursor would shrink with every page fetched and the
        UI would report a total that walked down to zero.
        """
        for index in range(3):
            await backend.red_team_reports.append(
                _record(execution_id=f"e{index}"),
            )
        first_page = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(), limit=2
        )
        boundary = first_page[-1]
        assert boundary.report_id is not None
        advanced = RedTeamReportFilterSpec(
            after_recorded_at=boundary.recorded_at,
            after_report_id=boundary.report_id,
        )

        assert await backend.red_team_reports.count(advanced) == 3
        assert (
            sum((await backend.red_team_reports.count_by_verdict(advanced)).values())
            == 3
        )
        # The page itself still honours the cursor; only the totals ignore it.
        assert len(await backend.red_team_reports.query(advanced)) == 1

    async def test_count_by_verdict_groups_in_one_read(
        self, backend: PersistenceBackend
    ) -> None:
        """The split comes back in one statement, zero-kinds omitted.

        A count per kind is a total assembled across as many instants as
        there are kinds, so a verdict landing mid-summary is counted in one
        and missing from another.
        """
        await backend.red_team_reports.append(
            _record(execution_id="b1", verdict=RedTeamVerdict.BLOCK),
        )
        await backend.red_team_reports.append(
            _record(execution_id="b2", verdict=RedTeamVerdict.BLOCK),
        )
        await backend.red_team_reports.append(
            _record(execution_id="p1", verdict=RedTeamVerdict.PASS, findings=()),
        )

        counts = await backend.red_team_reports.count_by_verdict(
            RedTeamReportFilterSpec()
        )

        assert counts == {
            RedTeamVerdict.BLOCK.value: 2,
            RedTeamVerdict.PASS.value: 1,
        }

    async def test_count_by_verdict_honours_the_filter(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.red_team_reports.append(
            _record(
                execution_id="a1",
                verdict=RedTeamVerdict.BLOCK,
                adversary=_Adversary(agent_id="adversary-a"),
            ),
        )
        await backend.red_team_reports.append(
            _record(
                execution_id="b1",
                verdict=RedTeamVerdict.BLOCK,
                adversary=_Adversary(agent_id="adversary-b"),
            ),
        )

        counts = await backend.red_team_reports.count_by_verdict(
            RedTeamReportFilterSpec(red_team_agent_id=NotBlankStr("adversary-a")),
        )

        assert counts == {RedTeamVerdict.BLOCK.value: 1}

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        base = datetime.now(UTC)
        await backend.red_team_reports.append(
            _record(execution_id="stale", recorded_at=base - timedelta(days=2)),
        )
        await backend.red_team_reports.append(
            _record(execution_id="fresh", recorded_at=base),
        )
        removed = await backend.red_team_reports.purge_before(base - timedelta(days=1))
        assert removed == 1
        remaining = await backend.red_team_reports.query(RedTeamReportFilterSpec())
        assert {r.execution_id for r in remaining} == {"fresh"}

    async def test_purge_before_rejects_naive(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.red_team_reports.purge_before(
                datetime(2025, 1, 1),  # noqa: DTZ001 -- naive on purpose
            )

    async def test_query_empty_when_no_match(self, backend: PersistenceBackend) -> None:
        page = await backend.red_team_reports.query(
            RedTeamReportFilterSpec(execution_id=NotBlankStr("absent")),
        )
        assert page == ()
