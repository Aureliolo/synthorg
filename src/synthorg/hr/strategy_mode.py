"""Strategic output mode for strategic-tier agents."""

from enum import StrEnum


class StrategicOutputMode(StrEnum):
    """Controls how strategic agents frame their recommendations.

    Applies to any executive-tier agent (role reporting depth <= 1: the
    CEO and its direct reports) or any agent with an explicit override.

    - ``option_expander``: Present all options with analysis through each lens.
    - ``advisor``: Recommend top 2-3 options with reasoning and caveats.
    - ``decision_maker``: Make a final recommendation with full justification.
    - ``context_dependent``: Resolves by reporting depth -- the executive
      tier (depth <= 1) maps to ``decision_maker``, others to ``advisor``.
    """

    OPTION_EXPANDER = "option_expander"
    ADVISOR = "advisor"
    DECISION_MAKER = "decision_maker"
    CONTEXT_DEPENDENT = "context_dependent"
