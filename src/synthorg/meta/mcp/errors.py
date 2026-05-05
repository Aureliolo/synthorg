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

from synthorg.core.domain_errors import ForbiddenError, ValidationError

GuardrailCode = Literal["missing_confirm", "missing_reason", "missing_actor"]


class ArgumentValidationError(ValidationError):
    """Raised when a required handler argument is missing or wrongly typed.

    Call sites use the module-level factory ``invalid_argument(name, expected)``
    rather than instantiating this class directly; the factory keeps the
    raise statement free of string literals so ruff's ``EM101`` rule passes.

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
    """Raised when a destructive-op call fails its guardrails.

    Guardrails are: ``confirm=True`` set, non-blank ``reason``, and a
    non-None ``actor``.  Each missing precondition yields a distinct
    ``violation`` value so operators can distinguish "caller forgot to
    confirm" from "caller is anonymous".

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


def invalid_argument(name: str, expected: str) -> ArgumentValidationError:
    """Build an ``ArgumentValidationError`` for a bad/missing argument.

    Factory function so ``raise invalid_argument(...)`` keeps the raise
    statement free of string literals (ruff ``EM101``).
    """
    return ArgumentValidationError(name, expected)


def guardrail_violation(
    violation: GuardrailCode,
    message: str,
) -> GuardrailViolationError:
    """Build a ``GuardrailViolationError`` with a stable violation code."""
    return GuardrailViolationError(violation, message)
