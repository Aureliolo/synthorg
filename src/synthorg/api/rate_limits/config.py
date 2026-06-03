"""Per-operation rate limit configuration (HTTP-facing alias).

Source-of-truth lives in :mod:`synthorg.config.rate_limits` so the
``synthorg.settings`` subsystem can consume it without crossing into
the API layer.
"""

from synthorg.config.rate_limits import PerOpRateLimitConfig

__all__ = ["PerOpRateLimitConfig"]
