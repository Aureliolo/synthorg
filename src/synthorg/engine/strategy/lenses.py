"""Strategic lens definitions for trendslop mitigation.

Provides 8 strategic lenses (4 default, 4 optional) that force agents
to evaluate recommendations from multiple perspectives.  Each lens
includes a prompt fragment injected into the system prompt when active.
"""

import copy
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import (
    STRATEGY_LENS_DEFINITION_INCOMPLETE,
    STRATEGY_LENS_LOOKUP_FAILED,
)

logger = get_logger(__name__)


class StrategicLens(StrEnum):
    """Strategic analysis lenses for anti-trendslop evaluation.

    Default lenses (always active unless overridden):
        ``contrarian``, ``risk_focused``, ``cost_focused``, ``status_quo``

    Optional lenses (enabled via config):
        ``customer_focused``, ``competitive_response``,
        ``implementation_feasibility``, ``historical_precedent``
    """

    # Default lenses
    CONTRARIAN = "contrarian"
    RISK_FOCUSED = "risk_focused"
    COST_FOCUSED = "cost_focused"
    STATUS_QUO = "status_quo"

    # Optional lenses
    CUSTOMER_FOCUSED = "customer_focused"
    COMPETITIVE_RESPONSE = "competitive_response"
    IMPLEMENTATION_FEASIBILITY = "implementation_feasibility"
    HISTORICAL_PRECEDENT = "historical_precedent"


class LensDefinition(BaseModel):
    """Definition and prompt fragment for a strategic lens.

    Attributes:
        name: Human-readable lens name.
        description: Short description of what this lens evaluates.
        prompt_fragment: Text injected into the system prompt when
            this lens is active.
        is_default: Whether this lens is active by default.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Human-readable lens name")
    description: NotBlankStr = Field(description="What this lens evaluates")
    prompt_fragment: NotBlankStr = Field(
        description="Prompt text for this lens",
    )
    is_default: bool = Field(
        default=False,
        description="Whether active by default",
    )


# ── Lens registry ──────────────────────────────────────────────

_RAW_DEFINITIONS: dict[StrategicLens, LensDefinition] = {
    StrategicLens.CONTRARIAN: LensDefinition(
        name="Contrarian",
        description=(
            "Forces consideration of the opposite position. "
            "What if the obvious choice is wrong?"
        ),
        prompt_fragment=(
            "For every recommendation, construct the strongest possible "
            "argument for the opposite approach. Identify at least one "
            "scenario where the contrarian position outperforms the "
            "recommended one."
        ),
        is_default=True,
    ),
    StrategicLens.RISK_FOCUSED: LensDefinition(
        name="Risk-Focused",
        description=(
            "Evaluates downside scenarios and failure modes. What could go wrong?"
        ),
        prompt_fragment=(
            "Identify the top 3 risks of each recommendation. For each "
            "risk, estimate likelihood (low/medium/high), impact "
            "(low/medium/high), and propose a specific mitigation. "
            "Flag any risk that could threaten the company's survival."
        ),
        is_default=True,
    ),
    StrategicLens.COST_FOCUSED: LensDefinition(
        name="Cost-Focused",
        description=(
            "Analyzes total cost of ownership and hidden costs. "
            "What will this really cost?"
        ),
        prompt_fragment=(
            "Calculate or estimate the full cost of each option, "
            "including hidden costs (opportunity cost, switching cost, "
            "training, maintenance, technical debt). Compare against "
            "doing nothing. Flag any option where true cost exceeds "
            "the stated budget by more than 20%."
        ),
        is_default=True,
    ),
    StrategicLens.STATUS_QUO: LensDefinition(
        name="Status Quo",
        description=(
            "Evaluates whether current approach is adequate. "
            "Is change actually necessary?"
        ),
        prompt_fragment=(
            "Explicitly evaluate whether the current approach (doing "
            "nothing or continuing as-is) is a viable option. Identify "
            "what is working well today. Quantify the expected benefit "
            "of change versus the disruption cost."
        ),
        is_default=True,
    ),
    StrategicLens.CUSTOMER_FOCUSED: LensDefinition(
        name="Customer-Focused",
        description=(
            "Evaluates impact on end users and customers. "
            "How does this affect the people we serve?"
        ),
        prompt_fragment=(
            "Evaluate each option from the customer's perspective. "
            "How does it affect user experience, reliability, and "
            "trust? Identify any option that optimizes for internal "
            "metrics at the expense of customer value."
        ),
        is_default=False,
    ),
    StrategicLens.COMPETITIVE_RESPONSE: LensDefinition(
        name="Competitive Response",
        description=(
            "Anticipates competitor reactions and market dynamics. "
            "How will the market respond?"
        ),
        prompt_fragment=(
            "Consider how competitors might respond to each option. "
            "Will this create a sustainable advantage or will it be "
            "quickly copied? Identify options that are strong because "
            "of company-specific context, not just general best "
            "practice."
        ),
        is_default=False,
    ),
    StrategicLens.IMPLEMENTATION_FEASIBILITY: LensDefinition(
        name="Implementation Feasibility",
        description=(
            "Assesses practical execution challenges. Can we actually pull this off?"
        ),
        prompt_fragment=(
            "Evaluate the practical feasibility of each option given "
            "current team capabilities, infrastructure, and bandwidth. "
            "Identify the hardest part of implementation and assess "
            "whether the team has done anything similar before."
        ),
        is_default=False,
    ),
    StrategicLens.HISTORICAL_PRECEDENT: LensDefinition(
        name="Historical Precedent",
        description=(
            "Draws on historical patterns and past decisions. "
            "What can we learn from history?"
        ),
        prompt_fragment=(
            "Identify historical precedents for the proposed "
            "strategies -- both from this company's past decisions "
            "and from the broader industry. Note whether similar "
            "strategies succeeded or failed and under what conditions."
        ),
        is_default=False,
    ),
}

# Validate completeness at import time.
_missing_lenses = set(StrategicLens) - set(_RAW_DEFINITIONS)
if _missing_lenses:
    _names = sorted(lv.value for lv in _missing_lenses)
    _msg = f"Missing lens definitions for: {_names}"
    logger.error(
        STRATEGY_LENS_DEFINITION_INCOMPLETE,
        missing_lenses=_names,
    )
    raise ValueError(_msg)

LENS_DEFINITIONS: Final[MappingProxyType[StrategicLens, LensDefinition]] = (
    MappingProxyType(copy.deepcopy(_RAW_DEFINITIONS))
)
del _RAW_DEFINITIONS

DEFAULT_LENSES: Final[tuple[StrategicLens, ...]] = tuple(
    lens for lens, defn in LENS_DEFINITIONS.items() if defn.is_default
)


def get_lens_definitions(
    lens_names: tuple[str, ...],
) -> tuple[LensDefinition, ...]:
    """Look up lens definitions by name.

    Args:
        lens_names: Lens names to look up (case-insensitive).

    Returns:
        Tuple of matching :class:`LensDefinition` objects.

    Raises:
        KeyError: If any lens name is not found.
    """
    results: list[LensDefinition] = []
    for name in lens_names:
        key = normalize_ascii_lowercase(name)
        try:
            lens = StrategicLens(key)
        except ValueError:
            available = sorted(lv.value for lv in StrategicLens)
            msg = f"Unknown lens {name!r}. Available: {available}"
            logger.warning(
                STRATEGY_LENS_LOOKUP_FAILED,
                lens_name=name,
                available=available,
            )
            raise KeyError(msg) from None
        results.append(LENS_DEFINITIONS[lens])
    return tuple(results)
