# module-kind: code
"""The task money ceiling's write-time guard.

``budget.run_hard_ceiling`` is refused at write time when no configured
connection could ever cross it, because an operator who reads a bound the run
does not have is the whole failure. ``Task.hard_ceiling`` overrides that
setting per task, so the same write reaching through the task door needs the
same refusal; guarding only the setting would leave the stricter, more
specific value as the unguarded one.

The predicate itself is not decided here. It lives in
``core.billing_enums.money_ceiling_can_bind``, which both this guard and the
settings rule call, so the two doors cannot start answering differently.
"""

from synthorg.api.state import AppState
from synthorg.core.billing_enums import BillingModel, money_ceiling_can_bind
from synthorg.core.domain_errors import ValidationError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_TASK_MONEY_CEILING_REFUSED
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_FIELD: str = "hard_ceiling"


async def guard_task_money_ceiling(
    app_state: AppState,
    updates: dict[str, object],
    *,
    task_id: str,
) -> None:
    """Refuse a per-task money ceiling no configured connection could cross.

    Only a positive value is judged. ``0`` is the documented opt-out and an
    omitted field changes nothing, so neither can leave an unbindable bound
    configured and believed.

    Args:
        app_state: Application state carrying the provider config resolver.
        updates: The field-value pairs this write will apply.
        task_id: Target task identifier, for the refusal log.

    Raises:
        ValidationError: When every configured connection bills by something
            a per-token cost cannot measure.
    """
    raw = updates.get(_FIELD)
    if not isinstance(raw, int | float) or raw <= 0:
        return
    configs = await config_resolver_of(app_state).get_provider_configs()
    if money_ceiling_can_bind(config.billing_model for config in configs.values()):
        return
    logger.warning(
        API_TASK_MONEY_CEILING_REFUSED,
        task_id=task_id,
        requested_ceiling=float(raw),
        connection_count=len(configs),
    )
    flat = BillingModel.FLAT_RATE.value
    msg = (
        f"Task {_FIELD} of {float(raw)} cannot bind: every configured"
        f" provider connection bills by something a per-token cost cannot"
        f" measure, so the accumulated cost it compares against stays at"
        f" zero for the life of this run. Set hard_token_ceiling instead,"
        f" which is counted on every provider, or declare a per-token"
        f" connection. A connection's billing model is its own field;"
        f" correct it there if one of these is not really {flat}."
    )
    raise ValidationError(msg)


__all__ = ["guard_task_money_ceiling"]
