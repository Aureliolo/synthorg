# module-kind: code
"""Pure constructor for the applied-promotion record.

Extracted from :mod:`synthorg.hr.promotion.service` so the service stays
within its module-size budget. The builder reads only its arguments and has
no dependency on the service instance.
"""

from datetime import datetime

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.hr.promotion.models import PromotionRecord, PromotionRequest


def build_promotion_record(
    request: PromotionRequest,
    *,
    identity: AgentIdentity,
    new_model_id: str | None,
    initiated_by: NotBlankStr,
    now: datetime,
) -> PromotionRecord:
    """Construct the immutable promotion record for an applied change.

    Args:
        request: The promotion request being applied.
        identity: Agent identity at apply time (source of the old model id).
        new_model_id: Newly-assigned model id, or ``None`` when unchanged.
        initiated_by: Audit attribution for the change.
        now: Effective timestamp of the applied change.

    Returns:
        The promotion record.
    """
    return PromotionRecord(
        agent_id=request.agent_id,
        agent_name=request.agent_name,
        old_level=request.current_level,
        new_level=request.target_level,
        direction=request.direction,
        evaluation=request.evaluation,
        approved_by=(
            NotBlankStr("auto") if request.approval_id is None else NotBlankStr("human")
        ),
        approval_id=request.approval_id,
        effective_at=now,
        initiated_by=initiated_by,
        model_changed=new_model_id is not None,
        old_model_id=(identity.model.model_id if new_model_id is not None else None),
        new_model_id=(NotBlankStr(new_model_id) if new_model_id is not None else None),
    )
