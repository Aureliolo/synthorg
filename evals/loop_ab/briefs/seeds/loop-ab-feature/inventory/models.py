"""Inventory domain models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    """A stock-keeping unit and the quantity held of it."""

    sku: str
    quantity: int

    def __post_init__(self) -> None:
        """Reject a negative holding."""
        if self.quantity < 0:
            msg = f"quantity for {self.sku!r} must be >= 0"
            raise ValueError(msg)
