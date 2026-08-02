"""Record a human's project decision into the project brain on resume.

When a ``request_project_decision`` park is approved, the answer (carried as
the decision reason) is recorded as a project-brain ``DECISION`` entry before
the parked agent resumes with the choice injected. This is the
actually-emitted successor to the dead ``arch:decide`` channel: the org's
shaping decisions land in the brain as first-class, queryable records.

The entry names the human as its author because the decision was theirs, so
the recorded reasoning must not be prose they did not write: see
:func:`_rationale`.

Best-effort and non-authoritative: it never short-circuits the resume flow
(the agent still resumes via the mid-execution path) and quietly no-ops when
the decision cannot be located or the brain is not wired, so a brain-write
fault never strands an approved decision.
"""

import json

from synthorg.api.controllers._conversational_resume import _reread_approval_item
from synthorg.api.state import AppState
from synthorg.approval.resume_annotations import (
    ResumeReasonProvenance,
    reason_provenance,
)
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import task_engine_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_BRAIN_RECORD_SKIPPED,
    APPROVAL_GATE_RESUME_FAILED,
)
from synthorg.project_brain.models import (
    BrainEntryStatus,
    DecisionPayload,
)
from synthorg.project_brain.state import ProjectBrainStateSlice

logger = get_logger(__name__)

#: Field bounds from ``project_brain.models`` (BrainTitle / BrainRationale /
#: BrainShortText); the recorded answer is clamped to fit so a long free-text
#: decision cannot fail model validation and lose the record.
_TITLE_MAX: int = 512
_TEXT_MAX: int = 4096

#: Recorded as the rationale when the human decided by picking an offered
#: option. The chosen writeup is the agent's prose, so it is recorded as the
#: outcome; what the human contributed was the choice.
_OPTION_RATIONALE: str = (
    "Chosen by the decision-maker from the options the agent offered. The "
    "recorded outcome is the agent's writeup of that option, not the "
    "decision-maker's own words."
)


async def record_project_decision(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str,
    decision_reason: str | None,
) -> None:
    """Record an approved ``decision:project`` answer as a brain DECISION.

    No-ops unless the approval is a decision park (``metadata['decision']``),
    was approved, carries the human's chosen answer, and is bound to a task
    that still exists. Never raises for a routine miss (each declined step
    says why, via :func:`_skip`); criticals propagate.

    Raises:
        MemoryError: Propagated unconditionally (non-recoverable).
        RecursionError: Propagated unconditionally (non-recoverable).
    """
    if not approved or not decision_reason or not decision_reason.strip():
        return _skip(approval_id, "not_an_approved_answer")
    item = await _reread_approval_item(app_state, approval_id)
    if item is None:
        return _skip(approval_id, "approval_unreadable")
    if item.metadata.get("decision") != "true":
        return _skip(approval_id, "not_a_decision_park")
    if item.task_id is None:
        return _skip(approval_id, "no_task_bound")

    brain_service = app_state.slice(ProjectBrainStateSlice).service
    if brain_service is None:
        return _skip(approval_id, "brain_not_wired")

    try:
        task = await task_engine_of(app_state).get_task(item.task_id)
        if task is None:
            return _skip(approval_id, "task_not_found")
        answer = decision_reason.strip()[:_TEXT_MAX]
        await brain_service.append_entry(
            project_id=NotBlankStr(str(task.project)),
            title=NotBlankStr(item.description[:_TITLE_MAX]),
            rationale=NotBlankStr(_rationale(item, answer)),
            status=BrainEntryStatus.ACCEPTED,
            author=NotBlankStr(decided_by),
            payload=DecisionPayload(
                decision_outcome=answer,
                alternatives=_decode_options(
                    item.metadata.get("options"), approval_id=approval_id
                ),
            ),
            related_task_ids=(NotBlankStr(item.task_id),),
        )
    except Exception as exc:  # noqa: BLE001 -- best-effort: never strand a decision
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            note="failed to record project decision in the brain",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _skip(approval_id: str, reason: str) -> None:
    """Record why this decision is not becoming a brain entry.

    Most of these are routine (an ordinary approval is not a decision fork),
    which is why they are DEBUG rather than WARNING. They are logged at all
    because the alternative is six silent exits: "the decision was made and
    the brain never heard about it" is otherwise undiagnosable.
    """
    logger.debug(
        APPROVAL_GATE_BRAIN_RECORD_SKIPPED,
        approval_id=approval_id,
        reason=reason,
    )


def _rationale(item: ApprovalItem, answer: str) -> str:
    """Return the reasoning to record, without misattributing its author.

    The brain's ``rationale`` is read back as the reasoning of the entry's
    ``author``. On the free-text path that is exactly what the answer is. On
    the option path the answer is the agent's own writeup of the option, and
    the human contributed a pick rather than prose, so recording the writeup
    as their reasoning would put words in their mouth: the entry says instead
    what they actually did, and the writeup stays in ``decision_outcome``
    where authorship is not implied.

    Returns:
        The rationale text for this decision's provenance.
    """
    if reason_provenance(item) is ResumeReasonProvenance.AGENT_OPTION:
        return _OPTION_RATIONALE
    return answer


def _decode_options(raw: str | None, *, approval_id: str) -> tuple[NotBlankStr, ...]:
    """Decode the JSON-array options metadata into non-blank option strings.

    A fault costs the entry its ``alternatives`` list, not the entry, so it is
    logged rather than raised: the recorded decision is still true, it just
    reads as though nothing else was on the table.

    Returns:
        The options offered to the human (empty for an open-ended decision or
        on any decode fault).
    """
    if not raw:
        return ()
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError) as exc:
        logger.warning(
            APPROVAL_GATE_BRAIN_RECORD_SKIPPED,
            approval_id=approval_id,
            reason="options_metadata_not_json",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()
    if not isinstance(decoded, list):
        logger.warning(
            APPROVAL_GATE_BRAIN_RECORD_SKIPPED,
            approval_id=approval_id,
            reason="options_metadata_not_a_list",
            actual_type=type(decoded).__name__,
        )
        return ()
    return tuple(NotBlankStr(str(opt)) for opt in decoded if str(opt).strip())
