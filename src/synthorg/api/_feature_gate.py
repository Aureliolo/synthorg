# module-kind: code
"""Live capability gate -- API-layer alias for the shared settings gate.

The implementation lives in :mod:`synthorg.settings.feature_gate` so the
``meta`` MCP handlers can gate without a runtime meta-to-api import. This module
re-exports it under the historical ``synthorg.api._feature_gate`` path that the
API controllers and chat capabilities already import.
"""

from synthorg.settings.feature_gate import ensure_feature_enabled

__all__ = ["ensure_feature_enabled"]
