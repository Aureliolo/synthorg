"""Factory for the pluggable charter-interview strategy.

Dispatches on the ``CharterConfig.interview_strategy`` discriminator.
There is no silent default: an unrecognised discriminator raises
``UnknownCharterStrategyError`` at construction time (the project-wide
pluggable-subsystems contract).
"""

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.strategy import (
    CharterInterviewStrategy,
    LLMCharterInterviewer,
)
from synthorg.meta.errors import UnknownCharterStrategyError
from synthorg.observability import get_logger
from synthorg.observability.events.charter import CHARTER_STRATEGY_UNKNOWN
from synthorg.providers.protocol import ConnectionSelector

logger = get_logger(__name__)

_LLM: str = "llm"


def build_charter_interview_strategy(
    config: CharterConfig,
    *,
    connections: ConnectionSelector,
    cost_tracker: CostTrackerProtocol | None = None,
) -> CharterInterviewStrategy:
    """Construct the interview strategy named by *config*.

    Args:
        config: Charter-interview configuration carrying the strategy
            discriminator.
        connections: Resolves the connection the interview pair names, for
            LLM-backed strategies.
        cost_tracker: Optional cost tracker for LLM accounting.

    Returns:
        The concrete interview strategy.

    Raises:
        UnknownCharterStrategyError: If the discriminator maps to no
            strategy.
    """
    if config.interview_strategy == _LLM:
        return LLMCharterInterviewer(
            connections=connections,
            cost_tracker=cost_tracker,
        )
    logger.warning(
        CHARTER_STRATEGY_UNKNOWN,
        interview_strategy=config.interview_strategy,
    )
    raise UnknownCharterStrategyError(strategy=config.interview_strategy)


__all__ = ["build_charter_interview_strategy"]
