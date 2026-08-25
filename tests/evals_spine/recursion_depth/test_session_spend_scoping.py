# module-kind: tests
"""Which of a shared ledger's records belong to the session reading it.

One ledger now serves a whole cell, so a session cannot take the ledger's whole
contents as its own. Two things separate them and both are load-bearing: the
task id, which tells concurrent leaves apart, and the count standing when the
session opened, which tells apart the sessions of one merge node, since its
assembly and its review run under the same task several times over.
"""

import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import cast

import pytest
import structlog

from evals.harness.stall_watch import ProgressTrackingLedger
from evals.harness.workspace import CellWorkspace
from evals.recursion_depth import session as session_module
from evals.recursion_depth.session import (
    OpenSession,
    SessionLimits,
    SweepDeps,
    run_session,
)
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import CurrencyCode
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.providers.errors import ProviderError
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_MERGE_TASK = "45099520-df94-5de7-8402-f5a2ced2986f"
_LEAF_TASK = "a1b2c3d4-0000-4000-8000-000000000001"


def _record(task_id: str, *, tokens: int) -> CostRecord:
    """One productive call against *task_id*.

    Returns:
        The record.
    """
    return CostRecord(
        task_id=task_id,
        provider="example-provider",
        model="example-capable-001",
        input_tokens=tokens,
        output_tokens=0,
        cost=0.0,
        currency=CurrencyCode("USD"),
        timestamp=datetime(2026, 8, 25, tzinfo=UTC),
        call_category=LLMCallCategory.PRODUCTIVE,
    )


def _identity() -> AgentIdentity:
    """An agent carrying an explicit pair, which the binding reads.

    Returns:
        The identity.
    """
    return AgentIdentity(
        id=as_uuid("builder"),
        name=NotBlankStr("Builder"),
        role=NotBlankStr("developer"),
        department=NotBlankStr("engineering"),
        model=ModelConfig(
            provider=NotBlankStr("example-provider"),
            model_id=NotBlankStr("example-capable-001"),
        ),
        hiring_date=date(2026, 1, 1),
    )


def _task() -> Task:
    """The leaf a failing session was running.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid("leaf"),
        title=NotBlankStr("Build the parser"),
        description=NotBlankStr("Implement the parser and its tests."),
        type=TaskType.DEVELOPMENT,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=sid("builder"),
        priority=Priority.MEDIUM,
        project=sid("sweep"),
        created_by=NotBlankStr("harness"),
    )


#: What the stand-in engine fails with, and what the test matches on.
_UPSTREAM_REFUSAL = "upstream refused"


class _RaisingEngine:
    """An engine whose run fails the way a spent retry ladder does."""

    async def run(self, **_kwargs: object) -> object:
        """Fail as an exhausted provider ladder does.

        Raises:
            ProviderError: Always.
        """
        raise ProviderError(_UPSTREAM_REFUSAL)


@contextlib.asynccontextmanager
async def _not_watching(*_args: object, **_kwargs: object) -> AsyncIterator[None]:
    """Stand in for the stall watch, which needs a live engine."""
    yield


def _session(
    ledger: ProgressTrackingLedger,
    *,
    task_id: str,
    already: int,
    engine: object = None,
) -> OpenSession:
    """A session over *ledger*, opened once *already* records stood.

    The engine is never touched by ``spend``, which is the whole point of the
    read being separable from the run, so it defaults to nothing; only the
    test that drives ``run_session`` supplies one.

    Returns:
        The session.
    """
    return OpenSession(
        engine=cast("AgentEngine", engine),
        ledger=ledger,
        label=f"{task_id}-attempt1",
        gateway_hosted=True,
        task_id=task_id,
        already=already,
    )


class TestOneCellsLedgerServesManySessions:
    """A shared ledger, and what each reader is entitled to."""

    async def test_a_concurrent_sibling_does_not_reach_this_session(self) -> None:
        ledger = ProgressTrackingLedger()
        await ledger.record(_record(_LEAF_TASK, tokens=100))
        await ledger.record(_record(_MERGE_TASK, tokens=900))

        spend = await _session(ledger, task_id=_LEAF_TASK, already=0).spend(turns=3)

        assert spend.tokens == 100

    async def test_a_second_session_of_one_task_reads_only_what_it_added(self) -> None:
        # A merge node's assembly and its review run under ONE task id, and
        # run_merge adds each read as a delta. An id-only read hands the second
        # session the first session's spend as well, so the round books it
        # twice, and a third round books the first three times.
        ledger = ProgressTrackingLedger()
        await ledger.record(_record(_MERGE_TASK, tokens=100))
        first = await _session(ledger, task_id=_MERGE_TASK, already=0).spend(turns=2)

        await ledger.record(_record(_MERGE_TASK, tokens=40))
        second = await _session(ledger, task_id=_MERGE_TASK, already=1).spend(turns=1)

        assert (first.tokens, second.tokens) == (100, 40)

    async def test_a_session_that_recorded_nothing_is_reported(self) -> None:
        ledger = ProgressTrackingLedger()

        with structlog.testing.capture_logs() as logs:
            await _session(ledger, task_id=_LEAF_TASK, already=0).spend(turns=7)

        assert [
            entry
            for entry in logs
            if entry["event"] == "evals.recursion_depth.spend_empty"
        ]

    async def test_a_session_that_took_no_turn_is_not_reported(self) -> None:
        # The resume path opens one of these whenever a first call fails every
        # retry. Nothing was spent, so nothing went missing, and escalating
        # here teaches a reader to discount the line that matters.
        ledger = ProgressTrackingLedger()

        with structlog.testing.capture_logs() as logs:
            await _session(ledger, task_id=_LEAF_TASK, already=0).spend(turns=0)

        assert not [
            entry
            for entry in logs
            if entry["event"] == "evals.recursion_depth.spend_empty"
        ]


class TestAFailedSessionsSpendIsWrittenDown:
    """A raising session builds no outcome, so the log is the only record.

    The read exists so a failed session's spend is not lost, and reading it
    while letting the exception propagate lost exactly that: no cell record,
    no journal row and no report ever sees the figure.
    """

    async def test_the_spend_of_a_raising_session_reaches_the_log(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ledger = ProgressTrackingLedger()
        await ledger.record(_record(_LEAF_TASK, tokens=1200))
        session = _session(
            ledger, task_id=_LEAF_TASK, already=0, engine=_RaisingEngine()
        )

        @contextlib.asynccontextmanager
        async def _open(
            *_args: object, **_kwargs: object
        ) -> AsyncIterator[OpenSession]:
            yield session

        monkeypatch.setattr(session_module, "open_session", _open, raising=True)
        monkeypatch.setattr(session_module, "watching", _not_watching, raising=True)

        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ProviderError, match=_UPSTREAM_REFUSAL),
        ):
            await run_session(
                cast("SweepDeps", None),
                identity=_identity(),
                task=_task(),
                workspace=cast("CellWorkspace", None),
                execution_id="d1-gated-r0-leaf",
                limits=SessionLimits(
                    max_turns=8, cost_ceiling=5.0, token_ceiling=100_000
                ),
            )

        failed = [
            entry
            for entry in logs
            if entry["event"] == "evals.recursion_depth.unit_failed_spend"
        ]
        assert [entry["tokens"] for entry in failed] == [1200]
