"""Re-export resilience configuration models.

Canonical definitions live in :mod:`ai_company.config.schema` to avoid
circular imports (config → providers → config).  This module re-exports
them so consumers can use ``from ai_company.providers.resilience.config
import RetryConfig``.
"""

from ai_company.config.schema import RateLimiterConfig, RetryConfig

__all__ = ["RateLimiterConfig", "RetryConfig"]
