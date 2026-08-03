# module-kind: declarative
"""The ambient prompt providers, snapshotted once per prompt build.

Both the house-style and the ask-policy layers are served by process-global
ambient providers an operator can hot-swap. Resolving each one ONCE at the top
of the render and threading that value through every read is what keeps a
layer's injected section and its entry in the sections manifest from
disagreeing mid-build. They travel together so a third scoped layer widens this
tuple rather than every signature between here and the injection.

The two reads are sequential, so the pair is not a point-in-time snapshot of
both globals at once: a hot-swap landing between them yields a combination that
never coexisted. That is deliberate and harmless. The layers are independent
subsystems with separate settings keys and separate subscribers, so there is no
cross-layer invariant to violate, and the outcome is indistinguishable from the
ordinary state between a settings write and the subscriber that picks it up.
Coupling the two globals behind one lock to buy pair-atomicity would join two
unrelated subsystems for nothing.

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
    """Read both ambient providers for one prompt build.

    The ONLY place the prompt path reads them: every consumer downstream takes
    the value it is given, so ``None`` there means "nothing was bound when this
    build started", never "go and look again".

    Returns:
        Each provider as bound at the moment it was read; later hot-swaps do
        not affect this build.
    """
    return PromptAmbientProviders(
        house_style=current_house_style_provider(),
        ask_policy=current_ask_policy_provider(),
    )


__all__ = ["PromptAmbientProviders", "current_prompt_providers"]
