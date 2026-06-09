"""MCP handler-side error types.

Raised by handler helpers (``require_arg``, ``require_admin_guardrails``)
when caller input is malformed or a guardrail has not been satisfied.  The
handler is expected to catch these and return an ``err(...)`` envelope to
the invoker; they are intentionally *not* system errors.

The ``domain_code`` class attribute carries the stable wire identifier used
in MCP error envelopes (``"invalid_argument"`` / ``"guardrail_violated"``).
This is the MCP-layer dispatch token consumed by callers; it sits alongside
the RFC 9457 ``error_code`` ClassVar (used by the HTTP layer) without
overlap.
"""

from typing import ClassVar, Literal

from synthorg.core.domain_errors import (
    ConflictError,
    ForbiddenError,
    ServiceUnavailableError,
    ValidationError,
)

GuardrailCode = Literal["missing_confirm", "missing_reason", "missing_actor"]


class ArgumentValidationError(ValidationError):
    """Raised when a required handler argument is missing or wrongly typed.

    Call sites instantiate this class directly:
    ``raise ArgumentValidationError(name, expected)``.  The constructor
    builds the message internally so ruff's ``EM101`` rule passes
    without an intermediate factory.

    Attributes:
        argument: Name of the offending argument.
        expected: Human-readable description of the expected type.
        domain_code: Stable wire identifier (``"invalid_argument"``).
    """

    domain_code: ClassVar[str] = "invalid_argument"

    def __init__(self, argument: str, expected: str) -> None:
        """Initialise with argument name and expected-type description.

        Args:
            argument: Name of the offending argument.
            expected: Human-readable description of the expected type.
        """
        message = f"Argument {argument!r} missing or not a {expected}"
        super().__init__(message)
        self.argument = argument
        self.expected = expected


class GuardrailViolationError(ForbiddenError):
    """Raised when an admin-op call fails its guardrails.

    Guardrails are: ``confirm=True`` set, non-blank ``reason``, and a
    non-None ``actor`` carrying an audit-usable identifier.  Each
    missing precondition yields a distinct ``violation`` value so
    operators can distinguish "caller forgot to confirm" from "caller
    is anonymous".

    Attributes:
        violation: One of ``"missing_confirm"``, ``"missing_reason"``,
            ``"missing_actor"``.
        domain_code: Stable wire identifier (``"guardrail_violated"``).
    """

    domain_code: ClassVar[str] = "guardrail_violated"

    def __init__(self, violation: GuardrailCode, message: str) -> None:
        """Initialise with a violation code and human-readable message.

        Args:
            violation: Which guardrail failed -- one of
                ``"missing_confirm"``, ``"missing_reason"``,
                ``"missing_actor"``.  Typed as a ``Literal`` so
                typos/callers passing free-form strings are caught at
                type-check time.
            message: Human-readable explanation.
        """
        super().__init__(message)
        self.violation = violation


class ToolRegistryFrozenError(ConflictError):
    """Raised on an attempt to register a tool after the registry froze.

    The domain registry freezes on its first read so the tool surface
    cannot drift mid-run; registering afterwards is an internal wiring
    bug, surfaced as a conflict rather than a bare ``RuntimeError``.
    """


class HandlerServiceNotWiredError(ServiceUnavailableError):
    """Raised when an MCP handler's backing service is absent post-bootstrap.

    The service is expected to be wired on ``app_state`` once startup
    completes; its absence is a misconfiguration surfaced as a 503 so the
    operator can see which dependency failed to wire, rather than an
    opaque ``RuntimeError``.

    Attributes:
        service: Name of the service that was not wired.
    """

    def __init__(self, service: str) -> None:
        """Initialise with the name of the unwired service.

        Args:
            service: Identifier of the service missing from ``app_state``.
        """
        message = f"{service} not wired on app_state"
        super().__init__(message)
        self.service = service
