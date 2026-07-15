# module-kind: service
"""The Layer 1 build/test oracle: a pure verdict over execution records.

``BuildTestOracle.evaluate`` classifies a task and reads its
``purpose='tests'`` :class:`CodeExecutionRecord` rows to decide whether
the work actually builds and its tests actually pass. The verdict uses
LATEST-run semantics (the newest test run decides) so a task that failed,
was reworked, and now passes is ``VERIFIED`` rather than blocked forever
by the old red run.

Fail-open vs fail-closed line: the oracle's own logic failing (records
store unwired) passes through, because a missing checker is structural
absence, not evidence; but for a REQUIRED code task, absent / failing /
unreadable test evidence fails CLOSED, because shipping unverified code as
"done" is the exact failure the oracle exists to prevent.
"""

import asyncio
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.completion_oracle.build_test_models import (
    GroundingRequirement,
    OracleEvaluation,
    OracleVerdict,
)
from synthorg.engine.completion_oracle.classifier import classify_grounding_requirement
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.completion_oracle import (
    BUILD_TEST_CHECKER_FAULT,
    BUILD_TEST_CHECKER_UNAVAILABLE,
    BUILD_TEST_GATE_BLOCKED,
    BUILD_TEST_GATE_EVALUATED,
)
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionFilterSpec,
    CodeExecutionPurpose,
    CodeExecutionRecord,
    CodeExecutionRecordRepository,
)

logger = get_logger(__name__)

_TEST_RECORD_QUERY_LIMIT: Final[int] = 1000
"""Upper bound on test records inspected per task.

Newest-first, so the latest run (index 0) drives the verdict regardless of
the cap; the bound only affects the ``tests_seen`` / ``tests_failed``
telemetry counts, mirroring the receipt validator's signal-query ceiling.
"""


class BuildTestOracle:
    """Deterministic build/test verdict over persisted execution records.

    Stateless: every input arrives through :meth:`evaluate`, so a single
    boot-wired instance serves every completing task.
    """

    async def evaluate(
        self,
        task: Task,
        *,
        records: CodeExecutionRecordRepository | None,
    ) -> OracleEvaluation:
        """Compute the build/test verdict for ``task``.

        Args:
            task: The completing task.
            records: The append-only code-execution record store, or
                ``None`` on a persistence-less boot (then the verdict is
                ``CHECKER_UNAVAILABLE`` and completion proceeds).

        Returns:
            The :class:`OracleEvaluation`; its ``blocks_completion``
            property is the single signal the gate and the run-outcome
            re-source both read.

        Raises:
            asyncio.CancelledError: Propagated when the record query is
                cancelled, so the awaiting parent observes the cancel.
        """
        requirement = classify_grounding_requirement(task)
        if records is None:
            logger.debug(
                BUILD_TEST_CHECKER_UNAVAILABLE,
                task_id=str(task.id),
                requirement=requirement.value,
            )
            return OracleEvaluation(
                verdict=OracleVerdict.CHECKER_UNAVAILABLE,
                requirement=requirement,
                reason=(
                    "Execution-record store is unwired; build/test grounding "
                    "cannot be evaluated (passing through)."
                ),
            )

        page = await self._query_records(task, requirement, records)
        if page is None:
            return self._checker_fault_result(requirement)

        evaluation = self._verdict_from_records(requirement, page)
        if evaluation.blocks_completion:
            logger.warning(
                BUILD_TEST_GATE_BLOCKED,
                task_id=str(task.id),
                verdict=evaluation.verdict.value,
                requirement=requirement.value,
                tests_seen=evaluation.tests_seen,
                tests_failed=evaluation.tests_failed,
            )
        else:
            logger.info(
                BUILD_TEST_GATE_EVALUATED,
                task_id=str(task.id),
                verdict=evaluation.verdict.value,
                requirement=requirement.value,
                tests_seen=evaluation.tests_seen,
            )
        return evaluation

    async def _query_records(
        self,
        task: Task,
        requirement: GroundingRequirement,
        records: CodeExecutionRecordRepository,
    ) -> tuple[CodeExecutionRecord, ...] | None:
        """Fetch the task's test records newest-first.

        Returns:
            The records tuple, or ``None`` when the query raised (the
            caller then applies the fail-closed checker-fault path).

        Raises:
            asyncio.CancelledError: Propagated when the query is cancelled.
        """
        spec = CodeExecutionFilterSpec(
            task_id=str(task.id),
            purpose=CodeExecutionPurpose.TESTS,
        )
        try:
            return await records.query(spec, limit=_TEST_RECORD_QUERY_LIMIT)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- a checker fault degrades to a fail-CLOSED
            # UNVERIFIED for a REQUIRED task (the None return is mapped by
            # _checker_fault_result), never a silent pass; surfacing it as a
            # raise would wedge the whole completion on a transient records-store
            # blip.
            reraise_critical(exc)
            logger.warning(
                BUILD_TEST_CHECKER_FAULT,
                task_id=str(task.id),
                requirement=requirement.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    @staticmethod
    def _checker_fault_result(
        requirement: GroundingRequirement,
    ) -> OracleEvaluation:
        """Verdict when the record query raised.

        A REQUIRED task cannot be confirmed to work, so it fails CLOSED to
        ``UNVERIFIED``; a non-code task has nothing to ground and passes.

        Returns:
            The fault-path evaluation.
        """
        if requirement is GroundingRequirement.REQUIRED:
            return OracleEvaluation(
                verdict=OracleVerdict.UNVERIFIED,
                requirement=requirement,
                reason=(
                    "Execution-record query failed for a code task; cannot "
                    "confirm the build / tests pass (failing closed)."
                ),
            )
        return OracleEvaluation(
            verdict=OracleVerdict.NOT_APPLICABLE,
            requirement=requirement,
            reason=(
                "Execution-record query failed, but the task does not require "
                "build/test grounding (passing through)."
            ),
        )

    @staticmethod
    def _verdict_from_records(
        requirement: GroundingRequirement,
        page: tuple[CodeExecutionRecord, ...],
    ) -> OracleEvaluation:
        """Apply the LATEST-run decision table over the fetched records.

        Returns:
            The evaluation. The newest record (``page[0]``) decides the
            pass/fail axis; ``page`` is newest-first per the repository
            contract.
        """
        tests_seen = len(page)
        tests_failed = sum(1 for record in page if not record.passed)
        if tests_seen == 0:
            if requirement is GroundingRequirement.REQUIRED:
                return OracleEvaluation(
                    verdict=OracleVerdict.UNVERIFIED,
                    requirement=requirement,
                    reason=(
                        "Code task produced no test run; there is no evidence "
                        "the work builds or its tests pass (failing closed)."
                    ),
                )
            return OracleEvaluation(
                verdict=OracleVerdict.NOT_APPLICABLE,
                requirement=requirement,
                reason="Task does not require build/test grounding.",
            )
        latest = page[0]
        if not latest.passed:
            return OracleEvaluation(
                verdict=OracleVerdict.BUILD_TEST_FAILED,
                requirement=requirement,
                reason=(
                    f"Latest test run failed (command {latest.command!r}, "
                    f"exit {latest.returncode}"
                    f"{', timed out' if latest.timed_out else ''})."
                ),
                tests_seen=tests_seen,
                tests_failed=tests_failed,
            )
        verdict = (
            OracleVerdict.VERIFIED
            if requirement is GroundingRequirement.REQUIRED
            else OracleVerdict.NOT_APPLICABLE
        )
        return OracleEvaluation(
            verdict=verdict,
            requirement=requirement,
            reason=(
                f"Latest test run passed (command {latest.command!r}); "
                f"{tests_seen} test run(s) inspected."
            ),
            tests_seen=tests_seen,
            tests_failed=tests_failed,
        )
