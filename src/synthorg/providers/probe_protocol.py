# module-kind: code
"""Collaborator protocol for requesting an out-of-cycle provider health probe.

Lives in its own leaf so a provider-mutation path can ask for an immediate
probe without importing the prober -- and, transitively, the connection
catalog and network validator -- into the provider-management import graph.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProviderProbeRequester(Protocol):
    """Requests an immediate health probe for a single provider."""

    async def probe_provider(self, name: str) -> None:
        """Probe *name* now, bypassing the periodic cycle's cadence.

        An implementation records the outcome against the provider's health
        rather than returning it. It may still raise (a resolver or DNS
        failure propagates), so a caller that must not fail on a probe error
        is responsible for its own containment; see
        ``ProviderManagementService._probe_after_mutation``.
        """
        ...
