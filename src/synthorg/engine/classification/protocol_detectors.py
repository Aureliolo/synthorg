"""Protocol-level detectors for delegation, review, and authority.

These detectors validate structural protocol compliance rather than
analysing conversation semantics.  They check delegation chain
integrity, review pipeline consistency, and authority boundary
adherence.
"""

from typing import TYPE_CHECKING

from synthorg.budget.coordination_config import (
    DetectionScope,
    ErrorCategory,
)
from synthorg.core.normalization import normalize_identifier
from synthorg.engine.classification.models import (
    ErrorFinding,
    ErrorSeverity,
)
from synthorg.engine.review.models import ReviewVerdict
from synthorg.observability import get_logger
from synthorg.observability.events.classification import (
    DETECTOR_COMPLETE,
    DETECTOR_START,
)

if TYPE_CHECKING:
    from synthorg.engine.classification.protocol import DetectionContext
    from synthorg.engine.loop_protocol import ExecutionResult

logger = get_logger(__name__)


class DelegationProtocolDetector:
    """Validates delegation protocol integrity.

    Checks:
    - Delegated tasks have a ``parent_task_id`` linking to the root.
    - Delegation chain does not contain the delegatee (circular
      delegation).  The ``delegatee == last delegator`` variant is
      a subset of the circular check because the last delegator is
      always present in the delegation chain.

    Supports only ``TASK_TREE`` scope because
    ``DetectionContext.delegation_requests`` is only populated by
    ``TaskTreeLoader``; at ``SAME_TASK`` scope the tuple is always
    empty, so the detector would never produce findings.
    """

    @property
    def category(self) -> ErrorCategory:
        """Error category this detector targets."""
        return ErrorCategory.DELEGATION_PROTOCOL_VIOLATION

    @property
    def supported_scopes(self) -> frozenset[DetectionScope]:
        """Detection scopes this detector can operate on."""
        return frozenset({DetectionScope.TASK_TREE})

    async def detect(
        self,
        context: DetectionContext,
    ) -> tuple[ErrorFinding, ...]:
        """Check delegation requests for protocol violations.

        Args:
            context: Detection context with delegation data.

        Returns:
            Tuple of delegation violation findings.
        """
        logger.debug(
            DETECTOR_START,
            detector="delegation_protocol",
            message_count=len(context.delegation_requests),
        )
        findings: list[ErrorFinding] = []

        for req in context.delegation_requests:
            task = req.task

            # Check: delegated task must have parent_task_id
            if task.parent_task_id is None:
                findings.append(
                    ErrorFinding(
                        category=self.category,
                        severity=ErrorSeverity.HIGH,
                        description=(
                            f"Delegated task '{task.id}' has no "
                            f"parent_task_id (broken delegation chain)"
                        ),
                        evidence=(
                            f"delegator={req.delegator_id}",
                            f"delegatee={req.delegatee_id}",
                            f"task_id={task.id}",
                        ),
                    ),
                )

            # Check: delegatee should not appear in delegation_chain
            # (indicates circular delegation, also covers the
            # delegatee == last delegator case by construction).
            if req.delegatee_id in task.delegation_chain:
                findings.append(
                    ErrorFinding(
                        category=self.category,
                        severity=ErrorSeverity.HIGH,
                        description=(
                            f"Delegatee '{req.delegatee_id}' appears "
                            f"in delegation chain of task '{task.id}' "
                            f"(circular delegation)"
                        ),
                        evidence=(
                            f"delegation_chain={task.delegation_chain!r}",
                            f"delegatee={req.delegatee_id}",
                        ),
                    ),
                )

        result = tuple(findings)
        logger.debug(
            DETECTOR_COMPLETE,
            detector="delegation_protocol",
            finding_count=len(result),
        )
        return result


class ReviewPipelineProtocolDetector:
    """Validates review pipeline protocol consistency.

    Checks:
    - PASS verdict requires at least one stage result.
    - PASS verdict must not have any FAIL stages.
    """

    @property
    def category(self) -> ErrorCategory:
        """Error category this detector targets."""
        return ErrorCategory.REVIEW_PIPELINE_VIOLATION

    @property
    def supported_scopes(self) -> frozenset[DetectionScope]:
        """Detection scopes this detector can operate on."""
        return frozenset({DetectionScope.TASK_TREE})

    async def detect(
        self,
        context: DetectionContext,
    ) -> tuple[ErrorFinding, ...]:
        """Check review results for protocol violations.

        Args:
            context: Detection context with review data.

        Returns:
            Tuple of review violation findings.
        """
        logger.debug(
            DETECTOR_START,
            detector="review_pipeline_protocol",
            message_count=len(context.review_results),
        )
        if not context.review_results:
            logger.debug(
                DETECTOR_COMPLETE,
                detector="review_pipeline_protocol",
                finding_count=0,
                reason="no review_results in context (pending #1170)",
            )
            return ()
        findings: list[ErrorFinding] = []

        for review in context.review_results:
            # Check: PASS verdict with no stages is suspicious
            if review.final_verdict == ReviewVerdict.PASS and not review.stage_results:
                findings.append(
                    ErrorFinding(
                        category=self.category,
                        severity=ErrorSeverity.MEDIUM,
                        description=(
                            f"Task '{review.task_id}' passed review "
                            f"with no stage results (empty pipeline)"
                        ),
                        evidence=(
                            f"task_id={review.task_id}",
                            f"final_verdict={review.final_verdict.value}",
                            "stage_count=0",
                        ),
                    ),
                )

            # Check: PASS verdict contradicting a FAIL stage
            if review.final_verdict == ReviewVerdict.PASS:
                failed = [
                    s for s in review.stage_results if s.verdict == ReviewVerdict.FAIL
                ]
                if failed:
                    stage_names = ", ".join(s.stage_name for s in failed)
                    findings.append(
                        ErrorFinding(
                            category=self.category,
                            severity=ErrorSeverity.HIGH,
                            description=(
                                f"Task '{review.task_id}' passed review "
                                f"despite failed stages: {stage_names}"
                            ),
                            evidence=(
                                f"final_verdict={review.final_verdict.value}",
                                f"failed_stages={stage_names}",
                            ),
                        ),
                    )

        result = tuple(findings)
        logger.debug(
            DETECTOR_COMPLETE,
            detector="review_pipeline_protocol",
            finding_count=len(result),
        )
        return result


class AuthorityBreachDetector:
    """Detects attempts to operate outside an agent's authority.

    Cross-references the execution's tool-invocation record and
    delegation attempts against the agent's configured permissions
    (see issue #228 §Authority breach detection):

    - **Denied tool invocation** (HIGH): any tool name recorded in
      ``TurnRecord.tool_calls_made`` or an assistant ``tool_calls``
      entry that appears in ``identity.tools.denied`` is flagged as
      an authority breach even if the tool checker blocked the call
      later -- the attempt itself is a behaviour signal.
    - **Unauthorised delegation target** (HIGH): any delegation
      whose delegator matches the agent and whose delegatee role
      is not in ``identity.authority.can_delegate_to`` (when that
      allow-list is non-empty) is flagged.
    - **Budget authority breach** (HIGH): when an explicit
      ``budget_limit`` is supplied *or* the agent's
      ``authority.budget_limit`` is > 0, execution cost exceeding
      that limit is flagged.  The explicit constructor argument
      wins when both are set (useful for test overrides and for
      decoupling the detector from the agent config in future).

    Args:
        budget_limit: Optional override for the authority budget
            limit.  When ``None``, the detector falls back to
            ``context.execution_result.context.identity.authority.budget_limit``.
    """

    def __init__(self, *, budget_limit: float | None = None) -> None:
        self._explicit_budget_limit = budget_limit

    @property
    def category(self) -> ErrorCategory:
        """Error category this detector targets."""
        return ErrorCategory.AUTHORITY_BREACH_ATTEMPT

    @property
    def supported_scopes(self) -> frozenset[DetectionScope]:
        """Detection scopes this detector can operate on."""
        return frozenset({DetectionScope.SAME_TASK, DetectionScope.TASK_TREE})

    async def detect(
        self,
        context: DetectionContext,
    ) -> tuple[ErrorFinding, ...]:
        """Check execution for authority boundary violations.

        Inspects ``context.execution_result`` for denied-tool
        invocations, unauthorised delegation attempts, and
        (optionally) budget overruns.  Never raises.

        Args:
            context: Detection context with execution data.

        Returns:
            Tuple of authority breach findings.
        """
        execution_result = context.execution_result
        conversation = execution_result.context.conversation
        logger.debug(
            DETECTOR_START,
            detector="authority_breach",
            message_count=len(conversation),
        )
        findings: list[ErrorFinding] = []

        findings.extend(self._detect_denied_tool_invocations(execution_result))
        findings.extend(
            self._detect_unauthorised_delegations(context),
        )
        findings.extend(self._detect_budget_breach(execution_result))

        result = tuple(findings)
        logger.debug(
            DETECTOR_COMPLETE,
            detector="authority_breach",
            finding_count=len(result),
        )
        return result

    def _detect_denied_tool_invocations(
        self,
        execution_result: ExecutionResult,
    ) -> list[ErrorFinding]:
        """Flag tool calls whose names appear in the denied list.

        Collects attempted tool names from both ``TurnRecord.tool_calls_made``
        (execution-side record) and assistant ``tool_calls`` entries
        (model-side request) and matches them case-insensitively
        against ``identity.tools.denied``.

        Args:
            execution_result: The agent's ``ExecutionResult``.

        Returns:
            Findings for each distinct denied tool invocation.
        """
        identity = execution_result.context.identity
        denied = {normalize_identifier(name) for name in identity.tools.denied}
        if not denied:
            return []

        attempted: list[str] = []
        for turn in execution_result.turns:
            attempted.extend(turn.tool_calls_made)
        for msg in execution_result.context.conversation:
            attempted.extend(call.name for call in msg.tool_calls)

        seen: set[str] = set()
        findings: list[ErrorFinding] = []
        for name in attempted:
            key = normalize_identifier(name)
            if key in seen or key not in denied:
                continue
            seen.add(key)
            findings.append(
                ErrorFinding(
                    category=self.category,
                    severity=ErrorSeverity.HIGH,
                    description=(
                        f"Agent attempted to invoke denied tool '{name}' "
                        f"(listed in tool_permissions.denied)"
                    ),
                    evidence=(
                        f"tool_name={name}",
                        f"denied_tools={sorted(identity.tools.denied)!r}",
                    ),
                ),
            )
        return findings

    def _detect_unauthorised_delegations(
        self,
        context: DetectionContext,
    ) -> list[ErrorFinding]:
        """Flag delegation attempts outside the agent's authority scope.

        When ``authority.can_delegate_to`` is non-empty (explicit
        allow-list), any delegation whose ``delegator_id`` matches
        this agent and whose delegatee is not on the allow-list
        produces a HIGH finding.  Empty allow-lists disable the
        check -- the company-wide default is "delegate anywhere".

        Args:
            context: The detection context carrying delegation requests.

        Returns:
            Findings for each unauthorised delegation attempt.
        """
        identity = context.execution_result.context.identity
        allowed = {
            normalize_identifier(role) for role in identity.authority.can_delegate_to
        }
        if not allowed:
            return []
        findings: list[ErrorFinding] = []
        for req in context.delegation_requests:
            if req.delegator_id != context.agent_id:
                continue
            target = normalize_identifier(req.delegatee_id)
            if target in allowed:
                continue
            findings.append(
                ErrorFinding(
                    category=self.category,
                    severity=ErrorSeverity.HIGH,
                    description=(
                        f"Agent attempted to delegate to '{req.delegatee_id}' "
                        f"which is not in authority.can_delegate_to"
                    ),
                    evidence=(
                        f"delegator={req.delegator_id}",
                        f"delegatee={req.delegatee_id}",
                        f"allowed={sorted(identity.authority.can_delegate_to)!r}",
                    ),
                ),
            )
        return findings

    def _detect_budget_breach(
        self,
        execution_result: ExecutionResult,
    ) -> list[ErrorFinding]:
        """Flag total execution cost exceeding the authority budget.

        The effective budget is ``budget_limit`` passed to the
        constructor when provided, otherwise the agent's
        ``authority.budget_limit`` if it is strictly positive.  A
        limit of ``0.0`` from the agent config disables the check.

        Args:
            execution_result: The agent's ``ExecutionResult``.

        Returns:
            A single-element list when the limit is exceeded,
            otherwise an empty list.
        """
        identity = execution_result.context.identity
        if self._explicit_budget_limit is not None:
            limit = self._explicit_budget_limit
        elif identity.authority.budget_limit > 0:
            limit = identity.authority.budget_limit
        else:
            return []
        # lint-allow: currency-aggregation -- TurnRecord carries no currency
        total_cost = sum(t.cost for t in execution_result.turns)
        if total_cost <= limit:
            return []
        return [
            ErrorFinding(
                category=self.category,
                severity=ErrorSeverity.HIGH,
                description=(
                    f"Execution cost {total_cost:.4f} exceeds "
                    f"authority budget limit {limit:.4f}"
                ),
                evidence=(
                    f"total_cost={total_cost:.4f}",
                    f"budget_limit={limit:.4f}",
                    f"turn_count={len(execution_result.turns)}",
                ),
            ),
        ]
