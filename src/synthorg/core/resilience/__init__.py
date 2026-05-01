"""General resilience primitives shared across services and workers.

The ``providers/resilience`` package is coupled to ``ProviderError``
semantics and provider-specific retry classification.  This package
holds the cross-cutting primitives that other layers (NATS publishes,
HTTP shippers, parse-failure self-correction loops) reach for when
they need bounded retry without a provider-error contract.
"""

from synthorg.core.resilience.general_retry import GeneralRetryHandler

__all__ = ["GeneralRetryHandler"]
