"""Shared lifecycle tuning constants for long-running services.

Centralises values that several independently-owned services would
otherwise each hardcode, so a policy change is a single edit and a
reader can tell the repetition is intentional rather than coincidental.
"""

from typing import Final

# Grace period a service waits for in-flight work to drain during a
# stop() before abandoning the wait. Shared by the background-loop
# services (backup scheduler, HR pruning, chief-of-staff monitor,
# provider health prober) so the policy is one edit, not one per service.
DEFAULT_DRAIN_TIMEOUT_SECONDS: Final[float] = 30.0

# Overall ceiling for draining every detached initiative tail at shutdown.
# The tails drain in series over two passes, so summing their individual
# 30s bounds would exceed a typical graceful-shutdown window; this caps the
# whole sequence to fit inside it while still allowing one genuinely slow
# tail to run its full per-drain budget.
INITIATIVE_TAIL_TOTAL_DRAIN_BUDGET_SECONDS: Final[float] = 40.0
