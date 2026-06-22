"""Work routing policies and their factory.

The discriminator (``coordination.routing_policy``) selects the
strategy; ``leaf-threshold`` is the shipped safe default.
"""

from typing import TYPE_CHECKING, Final

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.engine.pipeline.errors import WorkRoutingUndecidableError
from synthorg.engine.pipeline.policy.always_team import AlwaysTeamRoutingPolicy
from synthorg.engine.pipeline.policy.llm_judged import LlmJudgedRoutingPolicy
from synthorg.engine.pipeline.policy.protocol import WorkRoutingPolicy
from synthorg.engine.pipeline.policy.threshold import LeafThresholdRoutingPolicy
from synthorg.providers.protocol import CompletionProvider

ROUTING_POLICY_LEAF_THRESHOLD: Final[str] = "leaf-threshold"
ROUTING_POLICY_ALWAYS_TEAM: Final[str] = "always-team"
ROUTING_POLICY_LLM_JUDGED: Final[str] = "llm-judged"

VALID_ROUTING_POLICIES: Final[tuple[str, ...]] = (
    ROUTING_POLICY_LEAF_THRESHOLD,
    ROUTING_POLICY_ALWAYS_TEAM,
    ROUTING_POLICY_LLM_JUDGED,
)

__all__ = [
    "ROUTING_POLICY_ALWAYS_TEAM",
    "ROUTING_POLICY_LEAF_THRESHOLD",
    "ROUTING_POLICY_LLM_JUDGED",
    "VALID_ROUTING_POLICIES",
    "AlwaysTeamRoutingPolicy",
    "LeafThresholdRoutingPolicy",
    "LlmJudgedRoutingPolicy",
    "WorkRoutingPolicy",
    "build_work_routing_policy",
]


def build_work_routing_policy(
    discriminator: str,
    *,
    threshold: int,
    provider: CompletionProvider | None = None,
    model: str | None = None,
    cost_tracker: CostTrackerProtocol | None = None,
) -> WorkRoutingPolicy:
    """Construct the configured routing policy.

    Args:
        discriminator: One of :data:`VALID_ROUTING_POLICIES`.
        threshold: Leaf-threshold value (also used as the
            ``llm-judged`` deterministic fallback threshold).
        provider: Completion provider (required for ``llm-judged``).
        model: Model identifier (required for ``llm-judged``).
        cost_tracker: Optional cost tracker for ``llm-judged``.

    Returns:
        The constructed :class:`WorkRoutingPolicy`.

    Raises:
        WorkRoutingUndecidableError: If the discriminator is unknown,
            or ``llm-judged`` is selected without a provider + model.
    """
    if discriminator == ROUTING_POLICY_LEAF_THRESHOLD:
        return LeafThresholdRoutingPolicy(threshold=threshold)
    if discriminator == ROUTING_POLICY_ALWAYS_TEAM:
        return AlwaysTeamRoutingPolicy()
    if discriminator == ROUTING_POLICY_LLM_JUDGED:
        if provider is None or model is None:
            msg = (
                "llm-judged routing policy requires a provider and model; "
                "configure a provider or select a deterministic policy"
            )
            raise WorkRoutingUndecidableError(msg)
        return LlmJudgedRoutingPolicy(
            provider=provider,
            model=model,
            fallback=LeafThresholdRoutingPolicy(threshold=threshold),
            cost_tracker=cost_tracker,
        )
    msg = (
        f"Unknown routing policy {discriminator!r}; "
        f"valid options: {', '.join(VALID_ROUTING_POLICIES)}"
    )
    raise WorkRoutingUndecidableError(msg)
