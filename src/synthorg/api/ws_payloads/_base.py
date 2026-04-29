"""Shared Pydantic config for WebSocket payload models."""

from pydantic import ConfigDict

PAYLOAD_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)
"""Standard config for every typed WebSocket payload model.

Used by every ``Ws*Payload`` model in the ``synthorg.api.ws_payloads``
package.  Frozen models prevent accidental mutation post-validation;
``extra="forbid"`` enforces that every payload field is declared on
its model.
"""
