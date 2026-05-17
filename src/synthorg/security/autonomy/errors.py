"""Autonomy change-strategy error hierarchy.

Inherits :class:`DomainError` so the prefix-vs-category validator runs
on every subclass.
"""

from synthorg.core.domain_errors import DomainError


class AutonomyStrategyConfigError(DomainError):
    """Raised when an autonomy change-strategy composition is misconfigured.

    The factory raises this when a non-``HUMAN_ONLY`` strategy is
    selected but a signal provider it requires (e.g. the performance
    signal for ``PERFORMANCE_GATED``) was not supplied.
    """
