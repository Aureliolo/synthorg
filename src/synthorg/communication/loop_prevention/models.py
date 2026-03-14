"""Loop prevention check outcome model."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001


class GuardCheckOutcome(BaseModel):
    """Result of a single loop prevention check.

    Attributes:
        passed: Whether the check passed (delegation allowed).
        mechanism: Name of the mechanism that produced this outcome.
        message: Human-readable detail (empty on success).
    """

    model_config = ConfigDict(frozen=True)

    passed: bool = Field(description="Whether the check passed")
    mechanism: NotBlankStr = Field(
        description="Mechanism name (e.g. 'max_depth', 'ancestry')",
    )
    message: str = Field(
        default="",
        description="Human-readable detail",
    )

    @model_validator(mode="after")
    def _validate_passed_message(self) -> Self:
        """Enforce passed/message correlation."""
        if self.passed and self.message:
            msg = "message must be empty when passed is True"
            raise ValueError(msg)
        if not self.passed and not self.message.strip():
            msg = "message is required when passed is False"
            raise ValueError(msg)
        return self
