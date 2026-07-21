# module-kind: code
"""Explicit-provider-binding entry point for minting gateway tokens.

Minting is the single place the Explicit Provider Binding contract is
enforced for the gateway: a run token is issued only for a
fully-bound ``(provider, model)`` pair. An unbound :class:`ModelRef`
raises :class:`GatewayModelUnboundError` rather than letting the gateway
later auto-pick a provider for a bare model id.
"""

from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.core.types import NotBlankStr
from synthorg.llm.gateway_errors import GatewayModelUnboundError
from synthorg.llm.gateway_token import GatewaySigner, GatewayTokenClaims
from synthorg.settings.model_ref import ModelRef


def mint_run_token(  # noqa: PLR0913 -- token binding carries the full run context
    signer: GatewaySigner,
    *,
    execution_id: NotBlankStr,
    agent_id: NotBlankStr,
    task_id: NotBlankStr,
    ref: ModelRef,
    project_id: NotBlankStr | None = None,
    cost_ceiling: float | None = None,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    ttl_seconds: int,
) -> str:
    """Mint a per-run gateway token for an explicitly bound model.

    Args:
        signer: The gateway signer.
        execution_id: The agent execution id.
        agent_id: Agent attribution.
        task_id: Task attribution.
        ref: The resolved model reference; must be fully bound.
        project_id: Optional project attribution.
        cost_ceiling: Optional hard run cost ceiling.
        currency: Currency for the ceiling and recorded cost.
        ttl_seconds: Token lifetime in seconds.

    Returns:
        A signed per-run bearer token.

    Raises:
        GatewayModelUnboundError: If ``ref`` names no provider or no model.
    """
    if not ref.is_bound:
        raise GatewayModelUnboundError
    claims = GatewayTokenClaims(
        execution_id=execution_id,
        agent_id=agent_id,
        task_id=task_id,
        project_id=project_id,
        provider=ref.provider,
        model_id=ref.model_id,
        cost_ceiling=cost_ceiling,
        currency=currency,
    )
    return signer.mint(claims, ttl_seconds=ttl_seconds)
