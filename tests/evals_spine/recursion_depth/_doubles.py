"""Doubles the recursion-depth spine shares across its own modules.

A sweep binds placeholder model ids, which no catalogue grades, so every
module here that reaches selection needs the same ungraded reader. Declared
once because two copies of one double drift apart silently: a module that
tightened its own copy would go on passing while the behaviour it claims to
cover was only ever exercised against the looser one.
"""

from synthorg.engine.routing_policy import (
    CapabilityPolicy,
    CapabilityPolicyConfig,
    ResolvedAgentCapabilityReader,
)
from synthorg.providers.routing.models import ResolvedModel


class UngradedResolver:
    """A catalogue that grades nothing, which is the placeholder pairs' case."""

    def resolve_for_pair(self, provider_name: str, ref: str) -> ResolvedModel | None:
        """Grade nothing.

        Returns:
            ``None``, so the roster's own claim is what selection reads.
        """
        del provider_name, ref
        return None


def ungraded_capability() -> CapabilityPolicy:
    """Build the one capability policy a sweep judges with.

    Returns:
        The policy, reading an ungraded catalogue.
    """
    return CapabilityPolicy(
        config=CapabilityPolicyConfig(),
        reader=ResolvedAgentCapabilityReader(UngradedResolver()),
    )


__all__ = ["UngradedResolver", "ungraded_capability"]
