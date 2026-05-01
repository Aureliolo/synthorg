"""Event constants for the cross-cutting resilience layer.

Covers the non-provider retry handler in
``synthorg.core.resilience.general_retry``.  Provider-side resilience
events live alongside the provider error taxonomy.
"""

from typing import Final

CORE_RESILIENCE_INVALID_CONFIG: Final[str] = "core.resilience.invalid_config"
"""``GeneralRetryHandler.__init__`` rejected a constructor argument
(``max_attempts``, ``base``, or ``cap``) before raising ``ValueError``.
Carried fields: ``retry_event`` (the caller's retry event name),
``parameter``, ``value``, optional ``base`` for the cap-vs-base check,
and ``reason``."""
