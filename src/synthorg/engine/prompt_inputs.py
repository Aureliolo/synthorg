"""What one system-prompt build renders from, as one value.

The render engine passes the same dozen inputs through four layers (context
assembly, one render, the trimming loop, the result), and each layer restated
them as its own keyword list, so a field added to one had to be threaded by
hand through the other three. Trimming is what makes the bundle a value rather
than a bag: dropping a section replaces the bundle with one that lacks the
input, and the result is assembled from whichever bundle survived.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.budget.currency import CurrencyCode
from synthorg.core.agent import AgentIdentity
from synthorg.core.role import Role
from synthorg.core.tool_disclosure import ToolL1Metadata
from synthorg.engine.prompt_profiles import PromptProfile
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.providers.models import ToolDefinition

if TYPE_CHECKING:
    from synthorg.core.company import Company
    from synthorg.core.effective_autonomy import EffectiveAutonomy


@dataclass(frozen=True, slots=True, kw_only=True)
class PromptInputs:
    """The inputs a system prompt is rendered from.

    Attributes:
        agent: Agent identity.
        role: Optional role with description.
        available_tools: Tool definitions.
        l1_summaries: L1 metadata for system prompt injection.
        company: Optional company context; trimming may drop it.
        org_policies: Company-wide policy texts; trimming may drop them.
        effective_autonomy: Resolved autonomy for the current run.
        context_budget: Formatted context budget indicator string.
        currency: ISO 4217 currency code for budget displays.
        profile: Prompt profile controlling rendering verbosity.
        strategy_config: Strategy config for trendslop mitigation; trimming
            may drop it.
    """

    agent: AgentIdentity
    role: Role | None
    available_tools: tuple[ToolDefinition, ...]
    l1_summaries: tuple[ToolL1Metadata, ...]
    company: Company | None
    org_policies: tuple[str, ...]
    effective_autonomy: EffectiveAutonomy | None
    context_budget: str | None
    currency: CurrencyCode
    profile: PromptProfile | None
    strategy_config: StrategyConfig | None


__all__ = ["PromptInputs"]
