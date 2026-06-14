"""Shared lifecycle tuning constants for long-running services.

Centralises values that several independently-owned services would
otherwise each hardcode, so a policy change is a single edit and a
reader can tell the repetition is intentional rather than coincidental.
"""

from typing import Final

# Grace period a service waits for in-flight work to drain during a
# stop() before abandoning the wait. Shared by the background-loop
# services (backup scheduler, escalation sweeper / notifier, HR pruning,
# chief-of-staff monitor, provider health prober) that each previously
# inlined this value.
DEFAULT_DRAIN_TIMEOUT_SECONDS: Final[float] = 30.0
