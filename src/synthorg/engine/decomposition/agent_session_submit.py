# module-kind: adapter
"""The planning session's one terminal tool, and the sink it writes into.

A planning session's whole deliverable is a single call to
``submit_decomposition_plan``, so the tool and the holder the strategy reads
the plan back out of travel together. A malformed or style-refused submission
comes back as a tool error rather than an exception, which is what lets the
session correct and resubmit on its next turn instead of ending on it.
"""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from typing import Final, cast, override

from pydantic import JsonValue

from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._atomicity_gate import describe_unsplittable
from synthorg.engine.decomposition._mangled_arguments import (
    mangled_serialisation_hint,
)
from synthorg.engine.decomposition.atomicity import SubtaskAtomicityPolicy
from synthorg.engine.decomposition.llm_parse import args_to_decomposition_plan
from synthorg.engine.decomposition.llm_prompt import build_decomposition_tool
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.errors import (
    DecompositionError,
    DecompositionUnsplittableError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_ATOMICITY_CORRECTION_REQUESTED,
    DECOMPOSITION_SESSION_ARGUMENTS_MANGLED,
    DECOMPOSITION_SESSION_DIGEST_FALLBACK,
    DECOMPOSITION_SESSION_DUPLICATE_SUBMIT,
    DECOMPOSITION_SESSION_PLAN_REJECTED,
    DECOMPOSITION_SESSION_PLAN_RESUBMITTED,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

#: How many distinct refused submissions a session is remembered by. A session
#: is turn-bounded, so this only has to outlast one; it is a cap rather than a
#: budget, so a model cycling through many distinct bad plans cannot grow the
#: set without limit.
_REMEMBERED_REFUSALS: Final[int] = 32


class PlanCapture:
    """The one plan a session submits, and the only writer of it.

    The check-and-set lives here rather than in the tool because it has to be
    one step: a turn is free to emit two ``submit_decomposition_plan`` calls,
    the invoker runs them as siblings in one task group against this one
    instance, and a check in the tool would let both see an empty capture, so
    the duplicate goes unreported and which plan survives is whichever task
    finished last.

    Read-only from outside for the same reason the write is guarded: the
    session's loop asks this object whether it has a plan yet, and anything
    able to clear it could put a delivered session back to undelivered.

    Args:
        parent_task_id: The objective being planned, for the duplicate warning.
    """

    __slots__ = (
        "_lock",
        "_mangled",
        "_parent_task_id",
        "_plan",
        "_refused",
        "_unsplittable",
    )

    def __init__(self, parent_task_id: NotBlankStr) -> None:
        self._plan: DecompositionPlan | None = None
        self._parent_task_id = parent_task_id
        self._lock = asyncio.Lock()
        self._refused: dict[str, int] = {}
        self._mangled = 0
        self._unsplittable = False

    @property
    def plan(self) -> DecompositionPlan | None:
        """The plan submitted so far, or ``None`` while none has been."""
        return self._plan

    @property
    def declined_to_split(self) -> bool:
        """Whether the session's last refusal was one it could not comply with.

        The level that asked for this one acts on that and on nothing else: a
        session it could not widen leaves a valid parent plan standing, while
        a session that failed on anything else has to surface. Tracked here
        because the session ends with no plan either way, and by then the
        condition is otherwise a substring of an error message.
        """
        return self._unsplittable

    async def set(self, plan: DecompositionPlan) -> None:
        """Accept *plan*, reporting it when it supersedes another.

        Args:
            plan: The plan the session just submitted.
        """
        async with self._lock:
            if self._plan is not None:
                logger.warning(
                    DECOMPOSITION_SESSION_DUPLICATE_SUBMIT,
                    parent_task_id=self._parent_task_id,
                    previous_subtask_count=len(self._plan.subtasks),
                    new_subtask_count=len(plan.subtasks),
                )
            self._plan = plan

    async def record_refusal(self, digest: str, *, unsplittable: bool) -> int:
        """Count this refused submission and answer how often it has arrived.

        Under the same lock as :meth:`set` and for the same reason: a turn may
        emit two calls, the invoker runs them as siblings against this one
        instance, and a read-then-write in the tool would let both see a first
        submission.

        Args:
            digest: What the submitted arguments hash to.
            unsplittable: Whether this refusal was the size correction. Latest
                wins rather than sticky: a session that fixed its sizing and
                then submitted malformed arguments did not decline to split.

        Returns:
            How many times this exact submission has now been refused, so one
            means it is new.
        """
        async with self._lock:
            self._unsplittable = unsplittable
            seen = self._refused.pop(digest, 0) + 1
            # Bounded by eviction rather than by refusing to record, so the
            # cap costs the OLDEST answer instead of every answer after it: a
            # model cycling through distinct bad plans cannot grow the set,
            # and a repeat still reads as a repeat past the cap. Re-inserting
            # after the pop also makes a digest that just arrived the newest,
            # so what falls out is what has not been seen for longest.
            if len(self._refused) >= _REMEMBERED_REFUSALS:
                del self._refused[next(iter(self._refused))]
            self._refused[digest] = seen
            return seen

    async def record_mangled(self) -> int:
        """Count a call the transport mangled and answer the running total.

        Under the same lock as its siblings, for the same reason.

        Returns:
            How many calls this session has now lost to the transport.
        """
        async with self._lock:
            # Same latest-wins rule :meth:`record_refusal` applies, and for the
            # same reason: a call the transport mangled carried no plan at all,
            # so it says nothing about whether the unit can be split. Left set,
            # it makes the empty session raise the unsplittable error and the
            # recursive caller read a transport failure as a deliberate refusal
            # to split.
            self._unsplittable = False
            self._mangled += 1
            return self._mangled


class SubmitDecompositionPlanTool(BaseTool):
    """Terminal planning tool: the session submits its final plan through it.

    The schema mirrors the single-shot decomposition tool (so each subtask
    carries ``expected_artifacts`` + ``acceptance_criteria``); the parsed,
    id-remapped plan is captured for the strategy to return. A malformed
    submission surfaces as a tool error so the agent can correct and resubmit
    within the same session.
    """

    def __init__(
        self,
        *,
        parent_task_id: NotBlankStr,
        capture: PlanCapture,
        available_roles: tuple[NotBlankStr, ...] = (),
        objective_criteria: tuple[NotBlankStr, ...] = (),
        atomicity: SubtaskAtomicityPolicy | None = None,
    ) -> None:
        super().__init__(
            name="submit_decomposition_plan",
            description=(
                "Submit the final plan. Provide every item with its "
                "dependencies (only genuine ones, so independent work runs in "
                "parallel), an accountable owning role, calibrated stakes, "
                "expected_artifacts, and acceptance_criteria. Call this exactly "
                "once, last, after you have researched and self-reviewed."
            ),
            parameters_schema=build_decomposition_tool(
                available_roles
            ).parameters_schema,
            category=ToolCategory.OTHER,
        )
        self._parent_task_id = parent_task_id
        self._capture = capture
        self._available_roles = available_roles
        self._objective_criteria = objective_criteria
        self._atomicity = atomicity

    @override
    async def transport_fault(self, arguments: Mapping[str, object]) -> str | None:
        """Answer a collapsed call before the schema gets to refuse it.

        The collapse destroys ``subtasks``, so schema validation rejects the
        payload and ``execute`` is never reached: answering from there put the
        correction on a path nothing could take. The refusal the model would
        otherwise receive names a required field it filled in correctly, which
        sends it to rewrite a plan that was never the problem.

        Returns:
            The re-serialisation instruction, or ``None`` when the arguments
            arrived intact.
        """
        mangled = mangled_serialisation_hint(arguments)
        if mangled is None:
            return None
        logger.warning(
            DECOMPOSITION_SESSION_ARGUMENTS_MANGLED,
            parent_task_id=self._parent_task_id,
            mangled_calls=await self._capture.record_mangled(),
        )
        return mangled

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Parse + capture the submitted plan, or report a correctable error.

        Returns:
            A success result naming the accepted subtask count, or an error
            result describing why the plan was rejected so the agent retries.
        """
        try:
            plan = args_to_decomposition_plan(
                cast("dict[str, JsonValue]", arguments),
                self._parent_task_id,
                self._available_roles,
                self._objective_criteria,
            )
        except DecompositionError as exc:
            return await self._refuse(arguments, exc)
        # Asked here rather than after the session, because this IS the
        # session's correction channel: at the last level there is nowhere to
        # split into, so the plan is handed back for a wider one instead.
        oversized = describe_unsplittable(plan.subtasks, policy=self._atomicity)
        if oversized is not None:
            logger.info(
                DECOMPOSITION_ATOMICITY_CORRECTION_REQUESTED,
                parent_task_id=self._parent_task_id,
            )
            return await self._refuse(
                arguments, DecompositionUnsplittableError(oversized)
            )
        await self._capture.set(plan)
        return ToolExecutionResult(
            content=(
                f"Plan accepted with {len(plan.subtasks)} subtasks. You may stop now."
            ),
        )

    async def _refuse(
        self, arguments: dict[str, object], exc: DecompositionError
    ) -> ToolExecutionResult:
        """Refuse the submission, reframing it when it is an unchanged repeat.

        A byte-identical resubmission of a plan just refused carries no
        information: it cannot be accepted, and answering it with the wording
        that already failed to land buys the same turn again. Two of five
        repair rounds on one parent in a live run were exactly this. So the
        repeat is named, and the instruction becomes what to change rather than
        the "fix the issue" the model has now been told twice.

        Args:
            arguments: The submission, as the session emitted it.
            exc: Why it was refused.

        Returns:
            The refusal the agent reads.
        """
        reason = safe_error_description(exc)
        seen = await self._capture.record_refusal(
            _submission_digest(arguments),
            unsplittable=isinstance(exc, DecompositionUnsplittableError),
        )
        # Logged as well as returned: the rejection the agent reads is one tool
        # result, and the question an expensive session raises later is whether
        # it was handed the same one repeatedly, which only the log can answer.
        logger.info(
            DECOMPOSITION_SESSION_PLAN_REJECTED,
            parent_task_id=self._parent_task_id,
            error_type=type(exc).__name__,
            error=reason,
        )
        if seen == 1:
            return ToolExecutionResult(
                content=(
                    f"Plan rejected: {reason}. Fix the issue and call "
                    "submit_decomposition_plan again."
                ),
                is_error=True,
            )
        logger.warning(
            DECOMPOSITION_SESSION_PLAN_RESUBMITTED,
            parent_task_id=self._parent_task_id,
            submissions=seen,
            error_type=type(exc).__name__,
        )
        return ToolExecutionResult(
            content=(
                f"Plan rejected again, and it was byte-identical to the one "
                f"refused {seen - 1} time(s) already, so nothing about it can "
                f"be accepted this time either. The refusal is unchanged: "
                f"{reason}. Do not resend this plan. Change the specific item "
                f"the refusal names, and if you cannot see what to change, "
                f"restate the refusal in your own words first and then submit "
                f"a plan that differs in that respect."
            ),
            is_error=True,
        )


def _submission_digest(arguments: dict[str, object]) -> str:
    """Identify one submission by its content.

    Key-order-independent, because two submissions differing only in the order
    a serialiser emitted their keys are the same plan and would otherwise read
    as a correction. Falls back to the repr for anything JSON cannot take: a
    digest that cannot be computed must not collide with a real one, and an
    un-serialisable submission was going to be refused anyway.

    Returns:
        A hex digest of the submitted arguments.
    """
    try:
        payload = json.dumps(arguments, sort_keys=True, default=repr)
    except (TypeError, ValueError) as exc:
        # Logged because it should not happen: these arguments are already
        # decoded JSON. A provider emitting a shape that reaches here
        # repeatedly is a finding about that provider, and this is the only
        # place it would be visible.
        logger.debug(
            DECOMPOSITION_SESSION_DIGEST_FALLBACK,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        payload = repr(sorted(arguments.items(), key=lambda item: item[0]))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["PlanCapture", "SubmitDecompositionPlanTool"]
