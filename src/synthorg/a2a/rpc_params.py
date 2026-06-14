"""Typed param models for A2A JSON-RPC methods.

Each supported RPC method (``message/send``, ``tasks/get``,
``tasks/cancel``) has a frozen Pydantic model carrying a
``method`` ``Literal`` discriminator. ``A2ARpcParams`` is the
discriminated union used by the gateway to route a parsed
:class:`~synthorg.a2a.models.JsonRpcRequest` to the correct
typed handler signature; manual ``params.get(...)`` walks at the
handler boundary go away.

The :func:`parse_rpc_params` helper merges the envelope's method
into the params payload (envelope wins on conflict, blocking peers
that try to inject a ``method`` key inside ``params``) and returns
the typed variant. ``ValidationError`` is the only failure mode;
the gateway maps it to a ``-32602 Invalid params`` response.
"""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    TypeAdapter,
)

from synthorg.a2a.models import A2AMessage, JsonRpcRequest
from synthorg.core.boundary import parse_typed
from synthorg.core.types import NotBlankStr


class A2AMessageSendParams(BaseModel):
    """Typed params for the ``message/send`` RPC.

    Attributes:
        method: Discriminator literal (always ``"message/send"``).
        message: The A2A message envelope, validated as a typed
            :class:`~synthorg.a2a.models.A2AMessage`.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    method: Literal["message/send"] = Field(
        default="message/send",
        description="RPC method discriminator",
    )
    message: A2AMessage = Field(description="Inbound A2A message")


class A2ATaskGetParams(BaseModel):
    """Typed params for the ``tasks/get`` RPC.

    Attributes:
        method: Discriminator literal (always ``"tasks/get"``).
        id: Task identifier to look up.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    method: Literal["tasks/get"] = Field(
        default="tasks/get",
        description="RPC method discriminator",
    )
    id: NotBlankStr = Field(description="Task identifier")


class A2ATaskCancelParams(BaseModel):
    """Typed params for the ``tasks/cancel`` RPC.

    Attributes:
        method: Discriminator literal (always ``"tasks/cancel"``).
        id: Task identifier to cancel.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    method: Literal["tasks/cancel"] = Field(
        default="tasks/cancel",
        description="RPC method discriminator",
    )
    id: NotBlankStr = Field(description="Task identifier")


A2ARpcParams = Annotated[
    A2AMessageSendParams | A2ATaskGetParams | A2ATaskCancelParams,
    Discriminator("method"),
]
"""Discriminated union of typed A2A RPC params.

Pydantic uses the ``method`` literal on each variant to deserialize
into the correct typed model.
"""


_PARAMS_ADAPTER: TypeAdapter[A2ARpcParams] = TypeAdapter(A2ARpcParams)


def parse_rpc_params(rpc_request: JsonRpcRequest) -> A2ARpcParams:
    """Parse a JSON-RPC request envelope into typed RPC params.

    The envelope's ``method`` field overrides any ``method`` key
    inside ``params``: a peer that smuggles ``params={"method":
    "message/send", ...}`` while declaring ``method: "tasks/get"``
    on the envelope is rejected by the discriminator (its variant
    requires only ``id``, not ``message``).

    Args:
        rpc_request: A validated :class:`JsonRpcRequest`.

    Returns:
        One of :class:`A2AMessageSendParams`,
        :class:`A2ATaskGetParams`, or :class:`A2ATaskCancelParams`,
        chosen by the envelope's ``method`` field.

    Raises:
        ValidationError: When the params shape does not match the
            chosen variant. The gateway maps this to a JSON-RPC
            ``-32602 Invalid params`` response.
    """
    payload: dict[str, object] = {**rpc_request.params, "method": rpc_request.method}
    return parse_typed("a2a.jsonrpc", payload, _PARAMS_ADAPTER)


__all__ = [
    "A2AMessageSendParams",
    "A2ARpcParams",
    "A2ATaskCancelParams",
    "A2ATaskGetParams",
    "parse_rpc_params",
]
