# module-kind: code
"""Deferred MCP handler-map loaders for the meta feature manifest.

Each loader imports its service-heavy handler module only when the
composition root asks for the map, so importing ``meta/feature.py``
during feature discovery never pulls the handler import graph.
"""

from collections.abc import Mapping


def load_meta_mcp_handlers() -> Mapping[str, object]:
    """Return the meta ``{tool_name: ToolHandler}`` map."""
    from synthorg.meta.mcp.handlers.meta import META_HANDLERS  # noqa: PLC0415

    return META_HANDLERS


def load_analytics_mcp_handlers() -> Mapping[str, object]:
    """Return the analytics ``{tool_name: ToolHandler}`` map."""
    from synthorg.meta.mcp.handlers.analytics import (  # noqa: PLC0415
        ANALYTICS_HANDLERS,
    )

    return ANALYTICS_HANDLERS


def load_signals_mcp_handlers() -> Mapping[str, object]:
    """Return the signals ``{tool_name: ToolHandler}`` map."""
    from synthorg.meta.mcp.handlers.signals import SIGNAL_HANDLERS  # noqa: PLC0415

    return SIGNAL_HANDLERS
