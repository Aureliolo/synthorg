"""The demo feature's trivial service + its response payload."""

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr


class DemoGreeting(BaseModel):
    """The greeting payload the demo service returns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    greeting: NotBlankStr


class DemoService:
    """A trivial service returning a fixed greeting.

    Exists only to prove a feature's own service is discoverable and wired
    through the substrate without any central edits.
    """

    def __init__(self, greeting: str) -> None:
        """Store the greeting the service hands back.

        Args:
            greeting: The greeting text to return from :meth:`greet`.
        """
        self._greeting = greeting

    def greet(self) -> DemoGreeting:
        """Return the configured greeting.

        Returns:
            The greeting payload.
        """
        return DemoGreeting(greeting=self._greeting)
