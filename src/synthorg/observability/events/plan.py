"""Plan lifecycle event constants.

The durable plan's own state machine, from the greenlight shell through review,
dispatch, and execution rollup. Distinct from ``events.plan_review``, which
covers the stakeholder review panel that runs before the approval decision.
"""

from typing import Final

PLAN_TRANSITION: Final[str] = "plan.transition"
PLAN_TRANSITION_INVALID: Final[str] = "plan.transition.invalid"
PLAN_TRANSITION_CONFIG_ERROR: Final[str] = "plan.transition.config_error"
