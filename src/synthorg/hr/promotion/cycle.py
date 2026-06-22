# module-kind: code
"""Automatic promotion-cycle scan.

Drives the periodic sweep behind :meth:`PromotionService.run_cycle`: it
evaluates every active agent for promotion (then demotion), requests
eligible changes, and applies the ones the approval strategy
auto-approves. Changes that need human review create an approval item
and stay pending. Per-agent failures are logged and skipped so one bad
agent never aborts the sweep. The scan uses only the service's public
surface, so it lives outside ``service.py`` to keep that module within
its size budget.
"""

from synthorg.approval.enums import ApprovalStatus
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import PromotionCooldownError, PromotionError
from synthorg.hr.promotion.models import PromotionEvaluation, PromotionRecord
from synthorg.hr.promotion.service import PromotionService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.promotion import (
    PROMOTION_CYCLE_RAN,
    PROMOTION_EVALUATE_FAILED,
)

logger = get_logger(__name__)


async def run_promotion_cycle(
    service: PromotionService,
) -> tuple[PromotionRecord, ...]:
    """Scan active agents and apply auto-approved seniority changes.

    Args:
        service: The promotion service to drive.

    Returns:
        The records for changes applied during this cycle.
    """
    if not service.enabled:
        return ()
    identities = await service.registry.list_active()
    applied: list[PromotionRecord] = []
    for identity in identities:
        agent_id = NotBlankStr(str(identity.id))
        try:
            record = await _cycle_one(service, agent_id, identity=identity)
        except Exception as exc:  # noqa: BLE001 -- one agent must not abort the sweep
            reraise_critical(exc)
            logger.warning(
                PROMOTION_EVALUATE_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            continue
        if record is not None:
            applied.append(record)
    logger.info(
        PROMOTION_CYCLE_RAN,
        evaluated=len(identities),
        applied=len(applied),
    )
    return tuple(applied)


async def _cycle_one(
    service: PromotionService,
    agent_id: NotBlankStr,
    *,
    identity: AgentIdentity,
) -> PromotionRecord | None:
    """Evaluate one agent and apply an auto-approved change, if any.

    The ``identity`` is the one already loaded by ``list_active`` at the
    sweep boundary; threading it into the read-only evaluation step avoids
    re-fetching the same agent per evaluation. The request step is NOT
    threaded: it persists a pending promotion request, so it re-reads the
    identity authoritatively to avoid recording stale agent data if the
    agent changed after ``list_active``. The apply step likewise re-reads
    under its per-agent lock by design (authoritative pre-mutation read).

    Returns:
        The applied record, or ``None`` when nothing was applied.
    """
    if service.is_in_cooldown(agent_id):
        return None
    evaluation = await _evaluate_best(service, agent_id, identity=identity)
    if evaluation is None:
        return None
    try:
        request = await service.request_promotion(agent_id, evaluation)
    except (PromotionError, PromotionCooldownError) as exc:
        logger.warning(
            PROMOTION_EVALUATE_FAILED,
            agent_id=agent_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if request.status != ApprovalStatus.APPROVED:
        return None
    try:
        return await service.apply_promotion(request)
    except (PromotionCooldownError, PromotionError) as exc:
        # A cooldown / approval race between request_promotion and
        # apply_promotion is a per-agent skip, not a sweep abort.
        logger.warning(
            PROMOTION_EVALUATE_FAILED,
            agent_id=agent_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def _evaluate_best(
    service: PromotionService,
    agent_id: NotBlankStr,
    *,
    identity: AgentIdentity,
) -> PromotionEvaluation | None:
    """Return the first eligible promotion or demotion evaluation.

    Tries promotion first, then demotion, skipping the boundary
    ``PromotionError`` an agent at the top/bottom level raises. The
    pre-loaded ``identity`` is forwarded so neither evaluation re-fetches.

    Returns:
        An eligible evaluation, or ``None`` when neither applies.
    """
    for evaluate in (service.evaluate_promotion, service.evaluate_demotion):
        try:
            evaluation = await evaluate(agent_id, identity=identity)
        except PromotionError:
            continue
        if evaluation.eligible:
            return evaluation
    return None
