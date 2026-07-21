# module-kind: code
"""Typed errors for the OpenAI-compatible LLM gateway.

Each maps to a distinct :class:`ErrorCode` so the gateway controller can
render the right OpenAI-shaped error body and HTTP status, and so the
worker-side adapter can map a terminal budget response onto
``TerminationReason.BUDGET_EXHAUSTED`` without string matching.
``GatewayBudgetExhaustedError`` subclasses the budget layer's
``BudgetExhaustedError`` so existing engine catch handlers cover it.
"""

from typing import ClassVar

from synthorg.budget.errors import BudgetExhaustedError
from synthorg.core.domain_errors import UnauthorizedError, ValidationError
from synthorg.core.error_taxonomy import ErrorCode


class GatewayTokenInvalidError(UnauthorizedError):
    """Raised when the per-run gateway bearer is missing, malformed or expired (401).

    A distinct ``error_code`` lets the harness tell an auth failure apart
    from a generic 401 so it fails the run loudly rather than retrying an
    unusable credential.
    """

    default_message: ClassVar[str] = "Gateway token is missing, malformed or expired"
    error_code: ClassVar[ErrorCode] = ErrorCode.GATEWAY_TOKEN_INVALID


class GatewayModelUnboundError(ValidationError):
    """Raised when a request names a model with no explicit provider (422).

    The gateway preserves the Explicit Provider Binding contract: a bare
    or unbound model is rejected rather than auto-picked to whichever
    provider happens to serve an overlapping id.
    """

    default_message: ClassVar[str] = (
        "Model must resolve to an explicit (provider, model) pair; "
        "no provider is bound for the requested model"
    )
    error_code: ClassVar[ErrorCode] = ErrorCode.GATEWAY_MODEL_UNBOUND


class GatewayBudgetExhaustedError(BudgetExhaustedError):
    """Raised when a run crosses its hard token/cost ceiling at the gateway (402).

    Inherits :class:`BudgetExhaustedError` (an inheritance alias for the
    error-code-uniqueness gate) so the engine's existing budget-exhaustion
    handling applies unchanged; the distinct ``error_code`` lets the
    adapter map the gateway's terminal 402 onto
    ``TerminationReason.BUDGET_EXHAUSTED``.
    """

    error_code: ClassVar[ErrorCode] = ErrorCode.GATEWAY_BUDGET_EXHAUSTED
    default_message: ClassVar[str] = "Run token/cost ceiling exhausted at the gateway"
