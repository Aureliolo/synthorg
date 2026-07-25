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

        Implementations are best-effort: a probe failure is recorded as a
        health outcome, never raised into the caller's mutation.
        """
        ...
