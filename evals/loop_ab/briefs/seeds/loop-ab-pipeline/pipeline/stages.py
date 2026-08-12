"""The stages that ship with the package."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Double:
    """Doubles its input."""

    @property
    def name(self) -> str:
        """The name this stage is registered under.

        Returns:
            The registered name.
        """
        return "double"

    def run(self, value: int) -> int:
        """Double *value*.

        Returns:
            Twice the input.
        """
        return value * 2
