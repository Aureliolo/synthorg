# module-kind: code
"""LLM gateway feature state slice.

Holds the gateway request pipeline plus the per-run token signer. The
signer is shared: the controller verifies tokens with it and the
OpenHands execution loop mints run tokens with the same instance. Both
``None`` until the construction wirer builds them at boot; the controller
predicate leaves the route unmounted until then.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.api.gateway.service import GatewayService
from synthorg.llm.gateway_token import GatewaySigner


class GatewayStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the LLM gateway feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    service: GatewayService | None = None
    signer: GatewaySigner | None = None
