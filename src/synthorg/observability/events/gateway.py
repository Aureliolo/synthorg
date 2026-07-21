"""LLM gateway event constants."""

from typing import Final

GATEWAY_REQUEST_RECEIVED: Final[str] = "gateway.request.received"
GATEWAY_TOOL_INVOKED: Final[str] = "gateway.tool.invoked"
GATEWAY_DISPATCH_FAILED: Final[str] = "gateway.dispatch.failed"
GATEWAY_MODEL_UNBOUND: Final[str] = "gateway.model.unbound"
GATEWAY_BUDGET_KILL: Final[str] = "gateway.budget.kill"
GATEWAY_INJECTION_SUSPECTED: Final[str] = "gateway.injection.suspected"
GATEWAY_TOKEN_REJECTED: Final[str] = "gateway.token.rejected"  # noqa: S105 -- event name
GATEWAY_PROVIDER_UNAVAILABLE: Final[str] = "gateway.provider.unavailable"
