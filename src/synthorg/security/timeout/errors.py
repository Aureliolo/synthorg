"""Timeout / risk-classifier error hierarchy.

All inherit :class:`DomainError` so the prefix-vs-category validator
runs on every subclass (keeps the inherited ``INTERNAL`` defaults).
"""

from synthorg.core.domain_errors import DomainError


class RiskClassifierConfigError(DomainError):
    """Raised when a risk-tier-classifier composition is misconfigured.

    The factory raises this when a non-``DEFAULT`` classifier type is
    selected but a dependency it requires (e.g. the in-flight probe for
    ``WORKLOAD_ADAPTIVE``) was not supplied.
    """
