"""Conformance tests for ``CompletionOracleReportArchiveRepository``.

Runs against both backends (SQLite + Postgres) via the ``backend`` fixture.
"""

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleFinding,
    CompletionOracleReport,
    CompletionOracleReportRecord,
    CompletionOracleVerdict,
)
from synthorg.persistence.completion_oracle_report_protocol import (
    CompletionOracleReportFilterSpec,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.redteam.models import RedTeamSeverity

pytestmark = pytest.mark.integration


class _Reviewer(NamedTuple):
    """Who reviewed, whose work it was, and what the reviewer ran on.

    One value rather than five keywords: the five travel together on every
    row, and a reviewer named without the pair it dispatched on is exactly
    the half-attribution these columns exist to prevent.
    """

    agent_id: str = "completion-reviewer"
    executor_id: str = "executor-1"
    provider: str | None = None
    model_id: str | None = None
    capability: CapabilityLevel | None = None


_ANY_REVIEWER = _Reviewer()


def _optional(value: str | None) -> NotBlankStr | None:
    """Return the non-blank form of an optional column value.

    Returns:
        The wrapped value, or ``None``.
    """
    return None if value is None else NotBlankStr(value)


#: The one finding a rejecting fixture carries. Its content is irrelevant to
#: every assertion here (these tests are about the archive, not the verdict);
#: what matters is that a REJECT is constructible at all.
_A_FINDING = CompletionOracleFinding(
    severity=RedTeamSeverity.MEDIUM,
    description=NotBlankStr("The acceptance criterion is not evidenced."),
)


def _record(
    *,
    execution_id: str = "exec-001",
    task_id: str = "task-001",
    verdict: CompletionOracleVerdict = CompletionOracleVerdict.REJECT,
    summary: str = "Independent review complete.",
    recorded_at: datetime | None = None,
    reviewer: _Reviewer = _ANY_REVIEWER,
) -> CompletionOracleReportRecord:
    report = CompletionOracleReport(
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr(task_id),
        reviewer_agent_id=NotBlankStr(reviewer.agent_id),
        executor_agent_id=NotBlankStr(reviewer.executor_id),
        verdict=verdict,
        summary=NotBlankStr(summary),
        # A REJECT names what has to be fixed. The archive stores whatever
        # the gate produced, so a fixture that rejects while naming nothing
        # is not a shape the gate can hand it.
        findings=((_A_FINDING,) if verdict is CompletionOracleVerdict.REJECT else ()),
    )
    return CompletionOracleReportRecord(
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr(task_id),
        verdict=verdict,
        report=report,
        recorded_at=recorded_at or datetime.now(UTC),
        # The record's own party columns, not the report's: the gate stamps
        # who it selected, and those are the columns the filters, the
        # distinctness CHECK and the per-reviewer surface all read.
        reviewer_agent_id=NotBlankStr(reviewer.agent_id),
        executor_agent_id=NotBlankStr(reviewer.executor_id),
        reviewer_provider=_optional(reviewer.provider),
        reviewer_model_id=_optional(reviewer.model_id),
        reviewer_capability=reviewer.capability,
    )


class TestCompletionOracleReportArchiveRepository:
    async def test_append_and_query_by_execution(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.completion_oracle_reports.append(_record())

        page = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(execution_id=NotBlankStr("exec-001")),
        )
        assert len(page) == 1
        record = page[0]
        assert record.execution_id == "exec-001"
        assert record.verdict is CompletionOracleVerdict.REJECT
        # The reviewer / executor identities round-trip through report_json.
        assert record.report.reviewer_agent_id == "completion-reviewer"
        assert record.report.executor_agent_id == "executor-1"

    async def test_a_re_reviewed_execution_keeps_both_reports(
        self, backend: PersistenceBackend
    ) -> None:
        """A row is one review event, not one execution.

        The gate runs again whenever a task is decided, re-opened and decided
        again, against the same recorded frame and so the same execution id.
        Keyed on that id alone, the second report collided and was swallowed,
        leaving the decision that actually stood with no evidence behind it
        while a superseded verdict remained the only record.
        """
        # One timestamp for both, deliberately. A re-review is driven by a
        # human decision arriving, not by a clock, so nothing spaces the two
        # writes out; and every column the sort was keyed on before the
        # archive key existed is one these two share by construction, which
        # left the read order for the case the key was added for undefined.
        escalated_at = datetime.now(UTC)
        await backend.completion_oracle_reports.append(
            _record(
                execution_id="dup",
                verdict=CompletionOracleVerdict.ESCALATE,
                recorded_at=escalated_at,
            ),
        )
        await backend.completion_oracle_reports.append(
            _record(
                execution_id="dup",
                verdict=CompletionOracleVerdict.APPROVE,
                recorded_at=escalated_at,
            ),
        )

        page = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(execution_id=NotBlankStr("dup")),
        )

        # Newest first, so the verdict that actually stood leads and the
        # superseded one is still there to read.
        assert [record.verdict for record in page] == [
            CompletionOracleVerdict.APPROVE,
            CompletionOracleVerdict.ESCALATE,
        ]

    async def test_query_newest_first_by_recorded_at(
        self, backend: PersistenceBackend
    ) -> None:
        base = datetime.now(UTC)
        await backend.completion_oracle_reports.append(
            _record(execution_id="old", recorded_at=base - timedelta(hours=1)),
        )
        await backend.completion_oracle_reports.append(
            _record(execution_id="new", recorded_at=base),
        )
        # Two records sharing a timestamp exercise the archive-key tie-breaker
        # the ORDER BY contract closes on: nothing else distinguishes them.
        await backend.completion_oracle_reports.append(
            _record(execution_id="tie-a", recorded_at=base + timedelta(hours=1)),
        )
        await backend.completion_oracle_reports.append(
            _record(execution_id="tie-b", recorded_at=base + timedelta(hours=1)),
        )
        page = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec()
        )
        assert [r.execution_id for r in page] == ["tie-b", "tie-a", "new", "old"]

    async def test_query_filters_by_task_and_verdict(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.completion_oracle_reports.append(
            _record(
                execution_id="e1", task_id="t1", verdict=CompletionOracleVerdict.REJECT
            ),
        )
        await backend.completion_oracle_reports.append(
            _record(
                execution_id="e2",
                task_id="t2",
                verdict=CompletionOracleVerdict.APPROVE,
                summary="Approved.",
            ),
        )
        by_task = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(task_id=NotBlankStr("t1")),
        )
        assert {r.execution_id for r in by_task} == {"e1"}
        rejected = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(verdict=CompletionOracleVerdict.REJECT),
        )
        assert {r.execution_id for r in rejected} == {"e1"}

    async def test_the_model_the_reviewer_ran_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        """Verdict quality is comparable per model only if the model is stored.

        The reviewer's current roster binding is not evidence of what ran when
        the verdict was reached, so the pair travels with the row.
        """
        await backend.completion_oracle_reports.append(
            _record(
                execution_id="bound",
                reviewer=_Reviewer(
                    provider="example-provider",
                    model_id="example-capable-001",
                    capability="capable",
                ),
            ),
        )
        page = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(execution_id=NotBlankStr("bound")),
        )
        assert page[0].reviewer_provider == "example-provider"
        assert page[0].reviewer_model_id == "example-capable-001"
        assert page[0].reviewer_capability == "capable"

    async def test_unset_attribution_reads_as_unknown(
        self, backend: PersistenceBackend
    ) -> None:
        """NULL is the honest value, never a fabricated attribution.

        Written through the current writer with the attribution left unset,
        which is the shape a degraded run produces; it is deliberately not a
        claim about a row that predates the columns, since ``append`` cannot
        write one.
        """
        await backend.completion_oracle_reports.append(_record(execution_id="legacy"))
        page = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(execution_id=NotBlankStr("legacy")),
        )
        assert page[0].reviewer_provider is None
        assert page[0].reviewer_model_id is None
        assert page[0].reviewer_capability is None

    async def test_query_filters_by_reviewer(self, backend: PersistenceBackend) -> None:
        await backend.completion_oracle_reports.append(
            _record(execution_id="e1", reviewer=_Reviewer(agent_id="reviewer-a")),
        )
        await backend.completion_oracle_reports.append(
            _record(execution_id="e2", reviewer=_Reviewer(agent_id="reviewer-b")),
        )
        page = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(
                reviewer_agent_id=NotBlankStr("reviewer-b")
            ),
        )
        assert {r.execution_id for r in page} == {"e2"}

    async def test_count_agrees_with_query_under_the_same_filter(
        self, backend: PersistenceBackend
    ) -> None:
        """A count derived from one page would report a window as a total."""
        reviewer_a = _Reviewer(agent_id="reviewer-a")
        for i in range(3):
            await backend.completion_oracle_reports.append(
                _record(execution_id=f"e{i}", reviewer=reviewer_a),
            )
        await backend.completion_oracle_reports.append(
            _record(execution_id="other", reviewer=_Reviewer(agent_id="reviewer-b")),
        )
        spec = CompletionOracleReportFilterSpec(
            reviewer_agent_id=NotBlankStr("reviewer-a")
        )
        assert await backend.completion_oracle_reports.count(spec) == 3
        first_page = await backend.completion_oracle_reports.query(spec, limit=2)
        assert len(first_page) == 2
        assert (
            await backend.completion_oracle_reports.count(
                CompletionOracleReportFilterSpec()
            )
            == 4
        )

    async def test_a_count_ignores_the_paging_cursor(
        self, backend: PersistenceBackend
    ) -> None:
        """The total answers the filter, never the caller's position in it.

        One spec legitimately serves both the page and the total, so a count
        that honoured the cursor would shrink with every page fetched and the
        UI would report a total that walked down to zero.
        """
        reviewer = _Reviewer(agent_id="reviewer-a")
        for i in range(3):
            await backend.completion_oracle_reports.append(
                _record(execution_id=f"e{i}", reviewer=reviewer),
            )
        spec = CompletionOracleReportFilterSpec(
            reviewer_agent_id=NotBlankStr("reviewer-a")
        )
        first_page = await backend.completion_oracle_reports.query(spec, limit=2)
        boundary = first_page[-1]
        assert boundary.report_id is not None
        advanced = CompletionOracleReportFilterSpec(
            reviewer_agent_id=NotBlankStr("reviewer-a"),
            after_recorded_at=boundary.recorded_at,
            after_report_id=boundary.report_id,
        )

        assert await backend.completion_oracle_reports.count(advanced) == 3
        assert (
            sum(
                (
                    await backend.completion_oracle_reports.count_by_verdict(advanced)
                ).values()
            )
            == 3
        )
        # The page itself still honours the cursor; only the totals ignore it.
        assert len(await backend.completion_oracle_reports.query(advanced)) == 1

    async def test_the_store_assigns_the_archive_key(
        self, backend: PersistenceBackend
    ) -> None:
        """Every row read back names its own position in the archive.

        The keyset cursor is built from it, so a backend that left it unset
        would page from a position naming nothing.
        """
        reviewed_at = datetime.now(UTC)
        await backend.completion_oracle_reports.append(
            _record(execution_id="k1", recorded_at=reviewed_at),
        )
        await backend.completion_oracle_reports.append(
            _record(execution_id="k2", recorded_at=reviewed_at),
        )

        page = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec()
        )

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
            await backend.completion_oracle_reports.append(
                _record(
                    execution_id=f"e{index}",
                    recorded_at=base - timedelta(minutes=index),
                ),
            )
        first = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(), limit=2
        )
        assert [r.execution_id for r in first] == ["e0", "e1"]

        # A newer verdict arrives between the two page reads. Under an offset
        # it would push ``e1`` into the second page and show it twice.
        await backend.completion_oracle_reports.append(
            _record(execution_id="late", recorded_at=base + timedelta(minutes=1)),
        )
        boundary = first[-1]
        assert boundary.report_id is not None
        second = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(
                after_recorded_at=boundary.recorded_at,
                after_report_id=boundary.report_id,
            ),
            limit=2,
        )

        assert [r.execution_id for r in second] == ["e2", "e3"]

    async def test_the_cursor_keeps_rows_sharing_an_instant_apart(
        self, backend: PersistenceBackend
    ) -> None:
        """Two reviews recorded at one instant page one at a time.

        A cursor comparing the timestamp alone would drop both rows at the
        boundary, which is exactly the case the surrogate key exists for.
        """
        reviewed_at = datetime.now(UTC)
        for index in range(3):
            await backend.completion_oracle_reports.append(
                _record(execution_id=f"same-{index}", recorded_at=reviewed_at),
            )
        first = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(), limit=1
        )
        boundary = first[0]
        assert boundary.report_id is not None

        rest = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(
                after_recorded_at=boundary.recorded_at,
                after_report_id=boundary.report_id,
            ),
        )

        assert len(rest) == 2
        assert boundary.execution_id not in {r.execution_id for r in rest}

    async def test_count_by_verdict_groups_in_one_read(
        self, backend: PersistenceBackend
    ) -> None:
        """The split comes back in one statement, zero-kinds omitted.

        A count per kind is a total assembled across as many instants as
        there are kinds, so a verdict landing mid-summary is counted in one
        and missing from another.
        """
        await backend.completion_oracle_reports.append(
            _record(execution_id="r1", verdict=CompletionOracleVerdict.REJECT),
        )
        await backend.completion_oracle_reports.append(
            _record(execution_id="r2", verdict=CompletionOracleVerdict.REJECT),
        )
        await backend.completion_oracle_reports.append(
            _record(execution_id="a1", verdict=CompletionOracleVerdict.APPROVE),
        )

        counts = await backend.completion_oracle_reports.count_by_verdict(
            CompletionOracleReportFilterSpec()
        )

        assert counts == {
            CompletionOracleVerdict.REJECT.value: 2,
            CompletionOracleVerdict.APPROVE.value: 1,
        }

    async def test_count_by_verdict_honours_the_filter(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.completion_oracle_reports.append(
            _record(
                execution_id="a1",
                verdict=CompletionOracleVerdict.REJECT,
                reviewer=_Reviewer(agent_id="reviewer-a"),
            ),
        )
        await backend.completion_oracle_reports.append(
            _record(
                execution_id="b1",
                verdict=CompletionOracleVerdict.REJECT,
                reviewer=_Reviewer(agent_id="reviewer-b"),
            ),
        )

        counts = await backend.completion_oracle_reports.count_by_verdict(
            CompletionOracleReportFilterSpec(
                reviewer_agent_id=NotBlankStr("reviewer-a")
            ),
        )

        assert counts == {CompletionOracleVerdict.REJECT.value: 1}

    async def test_the_table_refuses_a_self_review(
        self, backend: PersistenceBackend
    ) -> None:
        """No-self-review is structural, not a matter of trusting the caller.

        Now that reviewers are drawn from a roster where any agent can hold
        any role, the row-level guarantee matters more, not less. The report
        validator refuses a self-review first, so the record is built past it
        deliberately: what is under test is the layer that still holds when
        something upstream lies.
        """
        report = CompletionOracleReport.model_construct(
            execution_id=NotBlankStr("self"),
            task_id=NotBlankStr("task-001"),
            reviewer_agent_id=NotBlankStr("same-agent"),
            executor_agent_id=NotBlankStr("same-agent"),
            verdict=CompletionOracleVerdict.APPROVE,
            findings=(),
            summary=NotBlankStr("Approved my own work."),
            build_evidence_cited=False,
            test_evidence_cited=False,
            test_command=None,
        )
        record = CompletionOracleReportRecord.model_construct(
            execution_id=NotBlankStr("self"),
            task_id=NotBlankStr("task-001"),
            verdict=CompletionOracleVerdict.APPROVE,
            report=report,
            recorded_at=datetime.now(UTC),
            reviewer_agent_id=NotBlankStr("same-agent"),
            executor_agent_id=NotBlankStr("same-agent"),
            reviewer_provider=None,
            reviewer_model_id=None,
            reviewer_capability=None,
        )
        with pytest.raises(QueryError):
            await backend.completion_oracle_reports.append(record)

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        base = datetime.now(UTC)
        await backend.completion_oracle_reports.append(
            _record(execution_id="stale", recorded_at=base - timedelta(days=2)),
        )
        await backend.completion_oracle_reports.append(
            _record(execution_id="fresh", recorded_at=base),
        )
        removed = await backend.completion_oracle_reports.purge_before(
            base - timedelta(days=1)
        )
        assert removed == 1
        remaining = await backend.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec()
        )
        assert {r.execution_id for r in remaining} == {"fresh"}

    async def test_purge_before_rejects_naive(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.completion_oracle_reports.purge_before(
                datetime(2025, 1, 1),  # noqa: DTZ001 -- naive on purpose
            )
