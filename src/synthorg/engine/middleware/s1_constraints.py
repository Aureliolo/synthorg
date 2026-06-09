"""S1 constraint middleware implementations.

Concrete middleware for the four S1 risk mitigations:

1. AuthorityDeferenceGuard -- strips authority cues from transcripts
2. AssumptionViolationMiddleware -- detects broken assumptions
3. ClarificationGateMiddleware -- validates acceptance criteria
4. DelegationChainHashMiddleware -- records content hashes for drift
"""

import hashlib
import re
from typing import override

from synthorg.core.middleware_config import (
    AuthorityDeferenceConfig,
    ClarificationGateConfig,
)
from synthorg.engine.middleware.coordination_protocol import (
    BaseCoordinationMiddleware,
    CoordinationMiddlewareContext,
)
from synthorg.engine.middleware.errors import ClarificationRequiredError
from synthorg.engine.middleware.models import (
    AgentMiddlewareContext,
    AssumptionViolationEvent,
    AssumptionViolationType,
)
from synthorg.engine.middleware.protocol import BaseAgentMiddleware
from synthorg.observability import get_logger
from synthorg.observability.events.middleware import (
    MIDDLEWARE_ASSUMPTION_VIOLATION_DETECTED,
    MIDDLEWARE_AUTHORITY_DEFERENCE_DETECTED,
    MIDDLEWARE_CLARIFICATION_REQUIRED,
    MIDDLEWARE_DELEGATION_HASH_DRIFT,
    MIDDLEWARE_DELEGATION_HASH_RECORDED,
)

logger = get_logger(__name__)


# ── AuthorityDeferenceGuard (agent + coordination) ────────────────


class AuthorityDeferenceGuard(BaseAgentMiddleware):
    """Detects authority cues in transcripts (S1 S3 risk 2.2).

    Scans the incoming conversation history for imperative directives
    and authority-laden phrases, logs all matches for audit, and
    stores the justification header in metadata for downstream
    prompt injection.  Actual content redaction is deferred to
    prompt-builder integration.

    Args:
        config: Authority deference configuration.
    """

    def __init__(
        self,
        *,
        config: AuthorityDeferenceConfig | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="authority_deference")
        self._config = config or AuthorityDeferenceConfig()
        self._compiled = tuple(re.compile(p) for p in self._config.patterns)

    def scan(self, text: str) -> int:
        """Count authority cues in *text* across all configured patterns.

        Pure detection (no redaction), exposed so callers outside the
        agent-middleware path -- the interactive group chat, where prior
        contributions (possibly an authority-bearing role's) are fed to
        later participants in the same round -- can audit the same cues
        the middleware logs, reusing this guard's compiled patterns
        rather than rebuilding them. Returns ``0`` when the guard is
        disabled.

        Returns:
            Total number of authority-cue matches in *text*.
        """
        if not self._config.enabled:
            return 0
        return sum(len(pattern.findall(text)) for pattern in self._compiled)

    @override
    async def before_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Detect authority cues and store justification header.

        Returns:
            ``ctx`` with ``"authority_deference"`` metadata holding
            the detection count and justification-header config;
            ``ctx`` unchanged when the middleware is disabled.
        """
        if not self._config.enabled:
            return ctx

        # Detect authority cues in conversation messages
        detected_count = sum(
            self.scan(msg.content or "") for msg in ctx.agent_context.conversation
        )

        if detected_count > 0:
            logger.info(
                MIDDLEWARE_AUTHORITY_DEFERENCE_DETECTED,
                agent_id=ctx.agent_id,
                task_id=ctx.task_id,
                stripped_count=detected_count,
            )

        return ctx.with_metadata(
            "authority_deference",
            {
                "detected_count": detected_count,
                "justification_header": self._config.justification_header,
                "inject_header": detected_count > 0,
            },
        )


class AuthorityDeferenceCoordinationMiddleware(
    BaseCoordinationMiddleware,
):
    """Coordination-level authority deference (S1 S3 risk 2.2).

    Scans the rollup summary for authority-contaminated language
    before it gets written to the parent task.  Logs detections
    for audit; actual rollup content redaction is deferred to
    coordinator integration.

    Args:
        config: Authority deference configuration.
    """

    def __init__(
        self,
        *,
        config: AuthorityDeferenceConfig | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="authority_deference_coordination")
        self._config = config or AuthorityDeferenceConfig()
        self._compiled = tuple(re.compile(p) for p in self._config.patterns)

    @override
    async def before_update_parent(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Scan rollup for authority contamination.

        Returns:
            ``ctx`` with ``"authority_deference_coordination"``
            metadata holding the detection count; ``ctx`` unchanged
            when the middleware is disabled.
        """
        if not self._config.enabled:
            return ctx

        stripped_count = 0
        rollup = ctx.status_rollup
        if rollup is not None:
            rollup_str = str(rollup)
            for pattern in self._compiled:
                stripped_count += len(pattern.findall(rollup_str))

        if stripped_count > 0:
            task = ctx.coordination_context.task
            logger.info(
                MIDDLEWARE_AUTHORITY_DEFERENCE_DETECTED,
                task_id=str(task.id),
                stripped_count=stripped_count,
                context="coordination_rollup",
            )

        return ctx.with_metadata(
            "authority_deference_coordination",
            {"detected_count": stripped_count},
        )


# ── AssumptionViolationMiddleware ─────────────────────────────────

# Patterns that signal an assumption violation in model responses.
_PRECONDITION = AssumptionViolationType.PRECONDITION_CHANGED
_CRITERIA = AssumptionViolationType.CRITERIA_CONFLICT
_DEPENDENCY = AssumptionViolationType.DEPENDENCY_FAILED

_ASSUMPTION_VIOLATION_PATTERNS: tuple[
    tuple[str, AssumptionViolationType],
    ...,
] = (
    (r"(?i)precondition(?:s)?\s+(?:changed|no longer|violated)", _PRECONDITION),
    (r"(?i)(?:acceptance\s+)?criteria?\s+(?:conflict|contradict)", _CRITERIA),
    (r"(?i)dependency\s+(?:failed|unavailable|broken)", _DEPENDENCY),
    (r"(?i)(?:I(?:'m| am)\s+)?stuck\s+because\s+\S+\s+changed", _PRECONDITION),
    (r"(?i)(?:cannot|can't)\s+proceed\s+(?:because|since|as)", _PRECONDITION),
)


class AssumptionViolationMiddleware(BaseAgentMiddleware):
    """Detects broken assumptions in model responses (S1 S3 risk 3.2).

    Checks model responses for assumption-violation markers and
    emits ``AssumptionViolationEvent`` as an escalation signal.
    """

    def __init__(self, **_kwargs: object) -> None:
        super().__init__(name="assumption_violation")
        self._patterns = tuple(
            (re.compile(p), vtype) for p, vtype in _ASSUMPTION_VIOLATION_PATTERNS
        )

    @override
    async def after_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Check last model response for assumption violations.

        Returns:
            ``ctx`` with ``"assumption_violations"`` metadata appended
            when any pattern matched; ``ctx`` unchanged otherwise.
        """
        messages = ctx.agent_context.conversation
        if not messages:
            return ctx

        last_msg = messages[-1]
        if not last_msg.content:
            return ctx

        violations: list[AssumptionViolationEvent] = []
        turn_number = ctx.agent_context.turn_count or 1

        for pattern, vtype in self._patterns:
            match = pattern.search(last_msg.content)
            if match:
                event = AssumptionViolationEvent(
                    agent_id=ctx.agent_id,
                    task_id=ctx.task_id,
                    violation_type=vtype,
                    description=f"Detected: {vtype.value}",
                    evidence=match.group(0)[:200],
                    turn_number=turn_number,
                )
                violations.append(event)
                logger.warning(
                    MIDDLEWARE_ASSUMPTION_VIOLATION_DETECTED,
                    agent_id=ctx.agent_id,
                    task_id=ctx.task_id,
                    violation_type=vtype.value,
                    turn_number=turn_number,
                )

        if violations:
            existing = ctx.metadata.get("assumption_violations", ())
            existing_items = (
                tuple(existing) if isinstance(existing, tuple | list) else ()
            )
            return ctx.with_metadata(
                "assumption_violations",
                (*existing_items, *violations),
            )

        return ctx


# ── ClarificationGateMiddleware ───────────────────────────────────


class ClarificationGateMiddleware(BaseCoordinationMiddleware):
    """Validates acceptance criteria before decomposition (S1 S3 risk 3.3).

    Checks the parent task's acceptance criteria for specificity.
    Raises ``ClarificationRequiredError`` if criteria are too vague.

    Args:
        config: Clarification gate configuration.
    """

    def __init__(
        self,
        *,
        config: ClarificationGateConfig | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="clarification_gate")
        self._config = config or ClarificationGateConfig()
        self._compiled_generic = tuple(
            re.compile(p, re.IGNORECASE) for p in self._config.generic_patterns
        )

    @override
    async def before_decompose(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Validate acceptance criteria specificity.

        Returns:
            ``ctx`` unchanged when the criteria pass the gate (or the
            gate is disabled).

        Raises:
            ClarificationRequiredError: When acceptance criteria are
                missing, too short, or match a generic pattern.
        """
        if not self._config.enabled:
            return ctx

        task = ctx.coordination_context.task
        reasons: list[str] = []

        if not task.acceptance_criteria:
            reasons.append("no acceptance criteria defined")

        for criterion in task.acceptance_criteria:
            text = criterion.description.strip()
            if len(text) < self._config.min_criterion_length:
                reasons.append(f"criterion too short ({len(text)} chars): {text!r}")
            if any(p.search(text) for p in self._compiled_generic):
                reasons.append(f"criterion is generic: {text!r}")

        if reasons:
            logger.warning(
                MIDDLEWARE_CLARIFICATION_REQUIRED,
                task_id=str(task.id),
                reason_count=len(reasons),
                reasons=reasons,
            )
            raise ClarificationRequiredError(
                task_id=str(task.id),
                reasons=tuple(reasons),
            )

        return ctx


# ── DelegationChainHashMiddleware ─────────────────────────────────


def compute_task_content_hash(
    title: str,
    description: str,
    criteria: tuple[str, ...],
) -> str:
    """Compute SHA-256 hash of task content for drift detection.

    Args:
        title: Task title.
        description: Task description.
        criteria: Acceptance criteria descriptions.

    Returns:
        Hex-encoded SHA-256 hash.
    """
    content = f"{title}\n{description}\n{'|'.join(criteria)}"
    return hashlib.sha256(content.encode()).hexdigest()


class DelegationChainHashMiddleware(BaseAgentMiddleware):
    """Records content hash for delegation chain drift (S1 S3 risk 4.3).

    Computes a SHA-256 hash of the task's title, description, and
    acceptance criteria.  For root tasks, stores the hash as both
    ``delegation_chain_hash`` and ``root_task_content_hash`` in
    metadata.  For delegated tasks, compares against the root hash
    to detect and log drift.
    """

    def __init__(self, **_kwargs: object) -> None:
        super().__init__(name="delegation_chain_hash")

    @override
    async def before_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Compute and record task content hash.

        Returns:
            ``ctx`` with ``"delegation_chain_hash"`` (and, for root
            tasks, ``"root_task_content_hash"``) stamped into metadata.
        """
        task = ctx.task
        criteria = tuple(c.description for c in task.acceptance_criteria)
        content_hash = compute_task_content_hash(
            task.title,
            task.description,
            criteria,
        )

        logger.debug(
            MIDDLEWARE_DELEGATION_HASH_RECORDED,
            agent_id=ctx.agent_id,
            task_id=ctx.task_id,
            content_hash=content_hash[:16],
        )

        # Seed root hash for root tasks; check drift for delegated tasks
        if not task.parent_task_id:
            ctx = ctx.with_metadata(
                "root_task_content_hash",
                content_hash,
            )
        elif task.delegation_chain:
            root_hash = ctx.metadata.get("root_task_content_hash")
            if root_hash is not None and root_hash != content_hash:
                logger.warning(
                    MIDDLEWARE_DELEGATION_HASH_DRIFT,
                    agent_id=ctx.agent_id,
                    task_id=ctx.task_id,
                    parent_task_id=task.parent_task_id,
                    root_hash=root_hash,
                    current_hash=content_hash,
                )

        return ctx.with_metadata(
            "delegation_chain_hash",
            content_hash,
        )
