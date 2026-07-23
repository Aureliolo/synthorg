"""In-memory inventory store."""

from inventory.models import Item


class Store:
    """Holds stock levels keyed by SKU."""

    def __init__(self) -> None:
        self._items: dict[str, Item] = {}

    def add(self, item: Item) -> None:
        """Add *item*'s quantity to the stock held for its SKU."""
        existing = self._items.get(item.sku)
        held = existing.quantity if existing is not None else 0
        self._items[item.sku] = Item(sku=item.sku, quantity=held + item.quantity)

    def quantity(self, sku: str) -> int:
        """Total quantity held for *sku*, reserved or not.

        Returns:
            The held quantity, or 0 when the SKU is unknown.
        """
        item = self._items.get(sku)
        return item.quantity if item is not None else 0
