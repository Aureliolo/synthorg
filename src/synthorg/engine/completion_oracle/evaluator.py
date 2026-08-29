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
from pathlib import Path
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.completion_oracle.build_test_models import (
    GroundingRequirement,
    OracleEvaluation,
    OracleVerdict,
)
from synthorg.engine.completion_oracle.classifier import classify_grounding_requirement
from synthorg.engine.completion_oracle.pending_forgiveness import (
    declared_gates,
    failure_was_declared,
    unclaimed_criteria,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.completion_oracle import (
    BUILD_TEST_CHECKER_FAULT,
    BUILD_TEST_CHECKER_UNAVAILABLE,
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

    One boot-wired instance serves every completing task: the only state is
    where projects live on disk, which the whole deployment shares.

    Args:
        workspace_root: Base directory projects live under, so the verdict can
            read what a project declared pending. Without it a skeleton's
            by-design red suite reads as a broken build and the contract stage
            can never complete; ``None`` (a boot that resolved no workspace)
            forgives nothing rather than guessing at a directory.
    """

    __slots__ = ("_workspace_root",)

    def __init__(self, *, workspace_root: Path | None = None) -> None:
        self._workspace_root = workspace_root

    async def evaluate(
        self,
        task: Task,
        *,
        records: CodeExecutionRecordRepository | None,
    ) -> OracleEvaluation:
        """Compute the build/test verdict for ``task`` and record it.

        The act of a gate deciding a task's fate, so it logs. A surface
        that merely wants the verdict to render calls :meth:`verdict_for`
        instead: the computation is the same, and a listing is not an
        event.

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
        evaluation = await self.verdict_for(task, records=records)
        # Neutral evaluation event carrying a ``blocked`` field. The
        # enforcing BUILD_TEST_GATE_BLOCKED event is emitted only by the
        # adapter (apply_build_test_gate) when it actually reroutes the
        # task, so an enforced block is not counted twice.
        logger.info(
            BUILD_TEST_GATE_EVALUATED,
            task_id=str(task.id),
            verdict=evaluation.verdict.value,
            requirement=evaluation.requirement.value,
            tests_seen=evaluation.tests_seen,
            tests_failed=evaluation.tests_failed,
            blocked=evaluation.blocks_completion,
        )
        return evaluation

    async def verdict_for(
        self,
        task: Task,
        *,
        records: CodeExecutionRecordRepository | None,
    ) -> OracleEvaluation:
        """Compute the build/test verdict for ``task`` without recording it.

        A read surface asks this. The dashboard polls the approvals list,
        which resolves an oracle-block flag per finished task to render a
        badge, so the logging half ran on a cadence: three tasks written
        off hours earlier produced an INFO line each every thirty seconds,
        for ever, and the record of a gate actually deciding something sat
        in the same stream, indistinguishable.

        Args:
            task: The task whose run is being qualified.
            records: The append-only code-execution record store, or
                ``None`` on a persistence-less boot.

        Returns:
            The :class:`OracleEvaluation`.

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

        evaluation = self._verdict_from_records(task, requirement, page)
        if evaluation.blocks_completion or requirement is not (
            GroundingRequirement.REQUIRED
        ):
            return evaluation
        return await self._verdict_from_declared_gates(task, records, evaluation)

    async def _verdict_from_declared_gates(
        self,
        task: Task,
        records: CodeExecutionRecordRepository,
        evaluation: OracleEvaluation,
    ) -> OracleEvaluation:
        """Require a passing run of every gate the project declares.

        Asked only once the tests already passed, because a gate configuration
        is a definition of done and reporting the linter alongside a red suite
        buries the thing that actually broke.

        A declared gate with no passing run is a unit that is not finished. The
        alternative is what the manifest fields were before anything read them:
        a project declares how it lints, an agent never lints, and the operator
        reads a green badge over work no linter ever saw.

        Returns:
            The incoming evaluation, or a blocking one naming the gates that
            produced no passing run.

        Raises:
            asyncio.CancelledError: Propagated when a record query is cancelled.
        """
        gates = declared_gates(
            workspace_root=self._workspace_root, project_id=str(task.project)
        )
        unmet: list[str] = []
        for purpose in sorted(gates):
            page = await self._query_records_for(task, purpose, records)
            if page is None or not page or not page[0].passed:
                unmet.append(purpose.value)
        if not unmet:
            return evaluation
        return OracleEvaluation(
            verdict=OracleVerdict.BUILD_TEST_FAILED,
            requirement=evaluation.requirement,
            reason=(
                f"The project declares a {', '.join(unmet)} gate that this run"
                " produced no passing evidence for; run each declared command"
                " in the session that claims the work."
            ),
            tests_seen=evaluation.tests_seen,
            tests_failed=evaluation.tests_failed,
        )

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
        return await self._query_records_for(
            task, CodeExecutionPurpose.TESTS, records, requirement=requirement
        )

    async def _query_records_for(
        self,
        task: Task,
        purpose: CodeExecutionPurpose,
        records: CodeExecutionRecordRepository,
        *,
        requirement: GroundingRequirement = GroundingRequirement.REQUIRED,
    ) -> tuple[CodeExecutionRecord, ...] | None:
        """Fetch the task's records for one gate, newest-first.

        Returns:
            The records tuple, or ``None`` when the query raised.

        Raises:
            asyncio.CancelledError: Propagated when the query is cancelled.
        """
        spec = CodeExecutionFilterSpec(task_id=str(task.id), purpose=purpose)
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

    def _verdict_from_records(
        self,
        task: Task,
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
        if not latest.passed and not self._declared_failure(task):
            return OracleEvaluation(
                verdict=OracleVerdict.BUILD_TEST_FAILED,
                requirement=requirement,
                # The reason is logged and surfaced as the task transition
                # reason, so it must carry only safe status metadata: a command
                # line can embed credentials or other secret arguments.
                reason=(
                    f"Latest test run failed (exit {latest.returncode}"
                    f"{', timed out' if latest.timed_out else ''})."
                ),
                tests_seen=tests_seen,
                tests_failed=tests_failed,
            )
        outstanding = self._outstanding_criteria(task)
        if outstanding:
            return OracleEvaluation(
                verdict=OracleVerdict.BUILD_TEST_FAILED,
                requirement=requirement,
                reason=(
                    f"{len(outstanding)} of this task's criteria are still"
                    " listed pending in the project manifest; clear each"
                    " entry in the commit that implements it."
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
            reason=(f"Latest test run passed; {tests_seen} test run(s) inspected."),
            tests_seen=tests_seen,
            tests_failed=tests_failed,
        )

    def _declared_failure(self, task: Task) -> bool:
        """Whether the project declared exactly the failure this run produced.

        Returns:
            ``True`` when the manifest's pending set accounts for every
            failing test, which is what a correct skeleton produces.
        """
        return failure_was_declared(
            workspace_root=self._workspace_root,
            project_id=str(task.project),
        )

    def _outstanding_criteria(self, task: Task) -> tuple[str, ...]:
        """Which of *task*'s own criteria the manifest still calls pending.

        Asked of a run that is otherwise passing, which is the direction an
        exit status cannot see: a unit that implemented its criterion and left
        the marker behind exits zero, and the next unit inherits a criterion
        the manifest calls unimplemented.

        Asked only of a task that implements a plan item. A task carrying a
        plan id and no item id implements none: it is a stage job, and the
        skeleton stage is the one that WRITES these entries, so holding it to
        them would refuse every contract for doing its job.

        Returns:
            The task's criteria still listed pending.
        """
        if task.plan_item_id is None:
            return ()
        return unclaimed_criteria(
            [str(criterion.description) for criterion in task.acceptance_criteria],
            workspace_root=self._workspace_root,
            project_id=str(task.project),
        )
