"""The contract every pipeline stage implements."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Stage(Protocol):
    """One named transformation of an integer."""

    @property
    def name(self) -> str:
        """The name this stage is registered under."""
        ...

    def run(self, value: int) -> int:
        """Transform *value*."""
        ...
