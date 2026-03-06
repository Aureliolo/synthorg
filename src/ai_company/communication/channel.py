"""Channel domain model."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_company.communication.enums import ChannelType
from ai_company.core.types import (
    NotBlankStr,
    validate_unique_strings,
)


class Channel(BaseModel):
    """A named communication channel that agents can subscribe to.

    Attributes:
        name: Channel name (e.g. ``"#engineering"``).
        type: Channel delivery semantics.
        subscribers: Agent IDs subscribed to this channel.
    """

    model_config = ConfigDict(frozen=True)

    name: NotBlankStr = Field(description="Channel name")
    type: ChannelType = Field(
        default=ChannelType.TOPIC,
        description="Channel delivery semantics",
    )
    subscribers: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Agent IDs subscribed to this channel",
    )

    @model_validator(mode="after")
    def _validate_subscribers(self) -> Self:
        """Ensure subscriber entries are unique."""
        validate_unique_strings(self.subscribers, "subscribers")
        return self
