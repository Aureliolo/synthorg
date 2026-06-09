"""Structural protocol for the ``mem0`` Memory client subset the adapter uses.

Lives in a leaf module so ``adapter`` (which defines the concrete backend) and
the ``adapter_shared`` / ``shared`` mixins that annotate against it can all
import it at module level. Keeping it out of an ``if TYPE_CHECKING:`` block lets
runtime type-checking resolve the annotation instead of raising ``NameError``;
defining it here rather than in ``adapter`` avoids the ``adapter`` <->
``adapter_shared`` import cycle.
"""

from typing import Protocol


class Mem0Client(Protocol):
    """Subset of ``Memory`` methods used by the adapter."""

    def add(self, **kwargs: object) -> dict[str, object]:
        """Add.

        Returns:
            Mapping from ``str`` to ``object``.
        """
        ...

    def search(self, **kwargs: object) -> dict[str, object]:
        """Search.

        Returns:
            Mapping from ``str`` to ``object``.
        """
        ...

    def get_all(self, **kwargs: object) -> dict[str, object]:
        """Get all.

        Returns:
            Mapping from ``str`` to ``object``.
        """
        ...

    def get(self, memory_id: str) -> dict[str, object] | None:
        """Get.

        Returns:
            The matching ``dict[str, object]``, or ``None`` when no match is found.
        """
        ...

    def delete(self, memory_id: str) -> None:
        """Delete."""
        ...
