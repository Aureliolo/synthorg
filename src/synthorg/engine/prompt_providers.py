# module-kind: declarative
"""The ambient prompt providers, snapshotted once per prompt build.

Both the house-style and the ask-policy layers are served by process-global
ambient providers an operator can hot-swap. Resolving them once at the top of
the render and threading that single immutable snapshot through every read is
what keeps the injected sections and the sections manifest from disagreeing
mid-build. They travel together so a third scoped layer widens this tuple rather
than every signature between here and the injection.

A ``NamedTuple`` rather than a Pydantic model: the members are two runtime
protocols, and there is nothing to validate.
"""

from typing import NamedTuple

from synthorg.engine.ask_policy.provider import (
    AskPolicyProvider,
    current_ask_policy_provider,
)
from synthorg.engine.output_style.provider import (
    HouseStyleProvider,
    current_house_style_provider,
)


class PromptAmbientProviders(NamedTuple):
    """One immutable snapshot of the ambient providers for a prompt build.

    Attributes:
        house_style: The house-style provider, or ``None`` when unbound.
        ask_policy: The ask-policy provider, or ``None`` when unbound.
    """

    house_style: HouseStyleProvider | None = None
    ask_policy: AskPolicyProvider | None = None


def current_prompt_providers() -> PromptAmbientProviders:
    """Snapshot both ambient providers for one prompt build.

    Returns:
        The pair as bound right now; later hot-swaps do not affect this build.
    """
    return PromptAmbientProviders(
        house_style=current_house_style_provider(),
        ask_policy=current_ask_policy_provider(),
    )


__all__ = ["PromptAmbientProviders", "current_prompt_providers"]
