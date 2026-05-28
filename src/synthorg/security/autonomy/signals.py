"""Signal-provider protocols for autonomy change strategies (REWORK #9).

The performance-gated and budget-aware strategies depend on runtime
signals (HR rolling success rate, risk-unit budget headroom). They
take these as injected Protocols -- never concrete service imports --
so the security/autonomy layer stays decoupled from hr/ and budget/.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr


@runtime_checkable
class PerformanceSignalProvider(Protocol):
    """Supplies an agent's rolling task-success rate."""

    def success_rate(self, agent_id: NotBlankStr) -> float | None:
        """Return the agent's rolling success rate in ``[0.0, 1.0]``.

        Returns ``None`` when there is insufficient history to judge
        (the strategy treats ``None`` as "do not grant").
        """
        ...


@runtime_checkable
class RiskBudgetSignalProvider(Protocol):
    """Supplies remaining risk-unit budget headroom."""

    def headroom_fraction(self) -> float:
        """Return remaining risk-budget headroom as a fraction.

        ``1.0`` is a full budget, ``0.0`` is exhausted.
        """
        ...
