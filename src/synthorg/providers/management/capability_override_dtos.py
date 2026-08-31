"""DTO for the operator model-capability-override patch.

Carved out of ``capability_dtos.py`` to keep that module under the
500-line cap.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CapabilityOverridesUpdateRequest(BaseModel):
    """Partial-update payload for one model's capability overrides.

    Every field is a three-state override: omitting a field leaves that
    capability's existing override untouched, ``true``/``false`` sets it,
    and explicit ``null`` clears it (the resolved card/probe value stands
    again). Unlike a rate-limit patch, explicit ``null`` is a legitimate,
    meaningful value here -- it is how an operator retracts a previous
    override -- so it is never rejected.

    At least one field MUST be present in the body; an empty patch is
    rejected with HTTP 422.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    supports_tools: bool | None = Field(default=None)
    supports_vision: bool | None = Field(default=None)
    supports_streaming: bool | None = Field(default=None)
    supports_embeddings: bool | None = Field(default=None)
    supports_image_generation: bool | None = Field(default=None)
    supports_reasoning: bool | None = Field(default=None)
    supports_prompt_caching: bool | None = Field(default=None)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        """Reject an empty patch.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If no fields were set at all.
        """
        if not self.model_dump(exclude_unset=True):
            msg = (
                "capability-override patch must set at least one field; an "
                "empty patch has no effect"
            )
            raise ValueError(msg)
        return self
