"""Record a human's project decision into the project brain on resume.

When a ``request_project_decision`` park is approved, the human's chosen
option (carried as the decision reason) is recorded as a project-brain
``DECISION`` entry before the parked agent resumes with the choice
injected. This is the actually-emitted successor to the dead ``arch:decide``
channel: the org's shaping decisions land in the brain as first-class,
queryable records.

Best-effort and non-authoritative: it never short-circuits the resume flow
(the agent still resumes via the mid-execution path) and quietly no-ops when
the decision cannot be located or the brain is not wired, so a brain-write
fault never strands an approved decision.
"""

import json

from synthorg.api.controllers._conversational_resume import _reread_approval_item
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import task_engine_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
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
    whose project resolves. Never raises for a routine miss; criticals
    propagate.

    Raises:
        MemoryError: Propagated unconditionally (non-recoverable).
        RecursionError: Propagated unconditionally (non-recoverable).
    """
    if not approved or not decision_reason or not decision_reason.strip():
        return
    item = await _reread_approval_item(app_state, approval_id)
    if item is None or item.metadata.get("decision") != "true" or item.task_id is None:
        return

    brain_service = app_state.slice(ProjectBrainStateSlice).service
    if brain_service is None:
        return

    try:
        task = await task_engine_of(app_state).get_task(item.task_id)
        if task is None or task.project is None:
            return
        answer = decision_reason.strip()[:_TEXT_MAX]
        await brain_service.append_entry(
            project_id=NotBlankStr(str(task.project)),
            title=NotBlankStr(item.description[:_TITLE_MAX]),
            rationale=NotBlankStr(answer),
            status=BrainEntryStatus.ACCEPTED,
            author=NotBlankStr(decided_by),
            payload=DecisionPayload(
                decision_outcome=answer,
                alternatives=_decode_options(item.metadata.get("options")),
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


def _decode_options(raw: str | None) -> tuple[NotBlankStr, ...]:
    """Decode the JSON-array options metadata into non-blank option strings.

    Returns:
        The options offered to the human (empty for an open-ended decision or
        on any decode fault).
    """
    if not raw:
        return ()
    try:
        decoded = json.loads(raw)
    except ValueError, TypeError:
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(NotBlankStr(str(opt)) for opt in decoded if str(opt).strip())
