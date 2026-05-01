"""Per-operation inflight-concurrency configuration (HTTP-facing alias).

Source-of-truth lives in :mod:`synthorg.config.rate_limits` so the
``synthorg.settings`` subsystem can consume it without crossing into
the API layer (audit-144).
"""

from synthorg.config.rate_limits import PerOpConcurrencyConfig

__all__ = ["PerOpConcurrencyConfig"]
