"""Stakes-aware model routing event constants."""

from typing import Final

STAKES_ASSESSED: Final[str] = "stakes_routing.assessed"
STAKES_ROUTING_DECIDED: Final[str] = "stakes_routing.decided"
STAKES_ROUTING_TIER_ADJUSTED: Final[str] = "stakes_routing.tier_adjusted"
STAKES_ROUTING_COORD_NUDGE: Final[str] = "stakes_routing.coordination_nudge"
STAKES_ROUTING_RED_TEAM_MARKED: Final[str] = "stakes_routing.red_team_marked"
STAKES_ROUTING_TIER_UNRESOLVABLE: Final[str] = "stakes_routing.tier_unresolvable"
STAKES_ROUTING_BUDGET_OVERRODE: Final[str] = "stakes_routing.budget_overrode"
