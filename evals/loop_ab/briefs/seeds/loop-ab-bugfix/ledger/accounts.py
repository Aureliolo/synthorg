"""Account balance tracking.

Money is held as a list of signed entries; the balance is their sum, rounded to
whole cents so repeated float arithmetic cannot drift.
"""

from dataclasses import dataclass, field

CENTS_PRECISION = 2


@dataclass
class Account:
    """A named account holding an ordered list of signed entries."""

    name: str
    entries: list[float] = field(default_factory=list)

    def deposit(self, amount: float) -> None:
        """Record a deposit of *amount*."""
        self.entries.append(amount)

    def withdraw(self, amount: float) -> None:
        """Record a withdrawal of *amount*."""
        self.entries.append(amount)

    @property
    def balance(self) -> float:
        """Current balance across every recorded entry."""
        return sum(self.entries)
