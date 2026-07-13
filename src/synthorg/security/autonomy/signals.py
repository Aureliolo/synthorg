"""Signal-provider protocols for autonomy change strategies.

The budget-aware strategy depends on a runtime signal (risk-unit
budget headroom). It takes this as an injected Protocol -- never a
concrete service import -- so the security/autonomy layer stays
decoupled from budget/.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class RiskBudgetSignalProvider(Protocol):
    """Supplies remaining risk-unit budget headroom."""

    def headroom_fraction(self) -> float:
        """Return remaining risk-budget headroom as a fraction.

        ``1.0`` is a full budget, ``0.0`` is exhausted.
        """
        ...
