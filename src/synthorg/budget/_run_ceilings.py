"""The two per-run hard ceilings and the errors they raise.

A run is bounded by money and by tokens. They are two bounds on one
question, so they live together: a caller that reaches for one has the
other in front of it, and a wiring path cannot carry one and drop the
other without the omission being visible.
"""

from typing import Final, NamedTuple
from uuid import UUID

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.errors import (
    RunHardCeilingExceededError,
    RunHardTokenCeilingExceededError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_HARD_CEILING_EXCEEDED,
    BUDGET_HARD_TOKEN_CEILING_EXCEEDED,
)

logger = get_logger(__name__)


class MoneyCeiling(NamedTuple):
    """A per-run money ceiling and the currency it is denominated in.

    Paired because an amount without its code cannot be compared to
    anything: the same number means different money under a different
    setting, which is the failure ``assert_currencies_match`` exists to
    stop one layer up.

    Attributes:
        amount: Absolute ceiling in ``currency``; ``0.0`` disables it.
        currency: ISO 4217 code stamped on the raised error.
    """

    amount: float = 0.0
    currency: str = DEFAULT_CURRENCY


#: No ceiling at all, for a caller that has none to pass. A module-level
#: singleton because a call in a default argument is rejected by lint.
NO_MONEY_CEILING: Final[MoneyCeiling] = MoneyCeiling()


def raise_hard_ceiling(
    *,
    running_cost: float,
    ceiling: MoneyCeiling,
    agent_id: str,
    task_id: str | None,
    forecast_id: UUID | None,
) -> None:
    """Emit the ceiling-exceeded log + raise the typed error.

    Args:
        running_cost: Cost accumulated by the run so far.
        ceiling: The crossed money ceiling and its currency.
        agent_id: Agent identifier for logging.
        task_id: Task identifier carried on the error so the engine can
            route the parked context.
        forecast_id: Linked forecast row so the dashboard can show the
            original estimate beside the accumulated cost.

    Raises:
        RunHardCeilingExceededError: Always raised, after emitting the
            hard-ceiling-exceeded log event.
    """
    logger.error(
        BUDGET_HARD_CEILING_EXCEEDED,
        agent_id=agent_id,
        task_id=task_id,
        forecast_id=str(forecast_id) if forecast_id is not None else None,
        accumulated_cost=running_cost,
        hard_ceiling=ceiling.amount,
        currency=ceiling.currency,
    )
    msg = (
        f"Run hard ceiling exceeded: accumulated {running_cost:.4f} "
        f"{ceiling.currency} >= ceiling {ceiling.amount:.4f} {ceiling.currency}"
    )
    raise RunHardCeilingExceededError(
        msg,
        ceiling_amount=ceiling.amount,
        accumulated_cost=running_cost,
        currency=ceiling.currency,
        task_id=task_id,
        forecast_id=forecast_id,
    )


def raise_hard_token_ceiling(
    *,
    tokens_used: int,
    token_ceiling: int,
    agent_id: str,
    task_id: str | None,
) -> None:
    """Emit the token-ceiling-exceeded log + raise the typed error.

    No forecast is named: a forecast estimates money, and it has nothing to
    say about a token count. The run is raised and resumed through the
    ``budget.run_hard_token_ceiling`` setting or the task's own override.

    Args:
        tokens_used: Tokens accumulated by the run so far.
        token_ceiling: The crossed ceiling.
        agent_id: Agent identifier for logging.
        task_id: Task identifier carried on the error so the engine can
            route the parked context.

    Raises:
        RunHardTokenCeilingExceededError: Always raised, after emitting the
            token-ceiling-exceeded log event.
    """
    logger.error(
        BUDGET_HARD_TOKEN_CEILING_EXCEEDED,
        agent_id=agent_id,
        task_id=task_id,
        tokens_used=tokens_used,
        token_ceiling=token_ceiling,
    )
    msg = (
        f"Run hard token ceiling exceeded: accumulated {tokens_used} tokens "
        f">= ceiling {token_ceiling}. Raise Task.hard_token_ceiling or "
        f"budget.run_hard_token_ceiling and resume the parked run."
    )
    raise RunHardTokenCeilingExceededError(
        msg,
        token_ceiling=token_ceiling,
        tokens_used=tokens_used,
        task_id=task_id,
    )
