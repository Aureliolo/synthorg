# module-kind: code
"""What quota degradation used to offer, and what to do about it now.

Provider-swap-on-quota-exhaustion is retired: a provider is a registered
connection with its own credentials, endpoint and quota, so re-pointing a
run at another one would bill a quota nobody named. The setting that asked
for it can still be sitting in an operator's persisted config, which means
the retirement is not finished when the code is deleted: something has to
know the old names and what they should become.

That knowledge lives here rather than beside the model it constrains,
because the two directions want opposite things from it and both import
it: the model refuses a write, the config reader strips a read.
"""

from collections.abc import Mapping
from typing import Final

RETIRED_SWAP_KEYS: Final[frozenset[str]] = frozenset({"fallback_providers"})
RETIRED_SWAP_STRATEGY: Final[str] = "fallback"
SWAP_REPLACEMENT: Final[str] = (
    "quota exhaustion does not re-point a run at another connection: the "
    "agent's provider is marked unserviceable and the roster reassigns its "
    "work. Use strategy 'queue' to wait for the window, or 'alert' to refuse"
)
_REPLACEMENT_STRATEGY: Final[str] = "alert"


def strip_retired_degradation_settings(
    degradation: Mapping[str, object],
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Return *degradation* without the retired swap settings.

    Refusal is the right posture for a write: an operator asking for a
    provider swap is asking for something the system no longer does, and a
    value error naming the setting is how they find that out. It is the
    wrong posture for a read of a value already persisted, where the only
    reachable outcome is losing a connection the operator still has, over a
    setting whose correct value is now "absent". So a read strips it before
    validation, and the model keeps raising for everyone who did not come
    through this door.

    Args:
        degradation: The raw persisted degradation mapping.

    Returns:
        The cleaned mapping and the names of the settings removed from it,
        empty when there was nothing to strip.
    """
    cleaned = dict(degradation)
    stripped: list[str] = []
    for key in sorted(RETIRED_SWAP_KEYS & set(cleaned)):
        del cleaned[key]
        stripped.append(key)
    strategy = cleaned.get("strategy")
    if isinstance(strategy, str) and strategy.lower() == RETIRED_SWAP_STRATEGY:
        cleaned["strategy"] = _REPLACEMENT_STRATEGY
        stripped.append("strategy")
    return cleaned, tuple(stripped)
