# module-kind: code
"""Credentialed-tool MCP server boundary.

Exposes SynthOrg's governed external-action tools (forge / chat) as MCP
tools an embedded harness (OpenHands) consumes over a streamable-http
endpoint. Tool execution, credential brokering, the connection approval
gate, action-signature binding and egress pinning all run host-side, so
credentials never enter the agent sandbox; the harness only ever sees a
tool result (or an approval-parking notice). Visibility is actor-scoped
by this module's own ``visible_tool_names`` (mirroring the ``MCPToolScoper``
capability form: ``domain:action`` with ``*`` wildcards).
"""
