"""SecurityRule protocol — interface for synchronous rule evaluators."""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.security.models import SecurityContext, SecurityVerdict


@runtime_checkable
class SecurityRule(Protocol):
    """Protocol for a single security rule.

    Rules are evaluated synchronously in a chain.  Returning a
    ``SecurityVerdict`` means the rule matched; returning ``None``
    passes through to the next rule.
    """

    @property
    def name(self) -> str:
        """Human-readable rule name."""
        ...

    def evaluate(
        self,
        context: SecurityContext,
    ) -> SecurityVerdict | None:
        """Evaluate the rule against a security context.

        Args:
            context: The tool invocation context.

        Returns:
            A verdict if the rule matched, or ``None`` to pass through.
        """
        ...
