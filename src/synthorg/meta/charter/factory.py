"""Factory for the pluggable charter-interview strategy.

Dispatches on the ``CharterConfig.interview_strategy`` discriminator.
There is no silent default: an unrecognised discriminator raises
``UnknownCharterStrategyError`` at construction time (the project-wide
pluggable-subsystems contract).
"""

from synthorg.budget.tracker import CostTracker
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.strategy import (
    CharterInterviewStrategy,
    LLMCharterInterviewer,
)
from synthorg.meta.errors import UnknownCharterStrategyError
from synthorg.providers.protocol import CompletionProvider

_LLM: str = "llm"


def build_charter_interview_strategy(
    config: CharterConfig,
    *,
    provider: CompletionProvider,
    cost_tracker: CostTracker | None = None,
) -> CharterInterviewStrategy:
    """Construct the interview strategy named by *config*.

    Args:
        config: Charter-interview configuration carrying the strategy
            discriminator.
        provider: LLM completion provider for LLM-backed strategies.
        cost_tracker: Optional cost tracker for LLM accounting.

    Returns:
        The concrete interview strategy.

    Raises:
        UnknownCharterStrategyError: If the discriminator maps to no
            strategy.
    """
    if config.interview_strategy == _LLM:
        return LLMCharterInterviewer(
            provider=provider,
            config=config,
            cost_tracker=cost_tracker,
        )
    raise UnknownCharterStrategyError(strategy=config.interview_strategy)


__all__ = ["build_charter_interview_strategy"]
