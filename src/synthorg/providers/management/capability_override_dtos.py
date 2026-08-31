"""DTO for the operator model-capability-override patch.

Carved out of ``capability_dtos.py`` to keep that module under the
500-line cap.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Request fields that carry the deliberate-action ceremony rather than a
#: capability value; excluded from both the "at least one field" check and
#: the capability-override merge itself.
_GOVERNANCE_FIELDS: frozenset[str] = frozenset({"confirm", "reason"})


class CapabilityOverridesUpdateRequest(BaseModel):
    """Partial-update payload for one model's capability overrides.

    Every capability field is a three-state override: omitting a field
    leaves that capability's existing override untouched, ``true``/``false``
    sets it, and explicit ``null`` clears it (the resolved card/probe value
    stands again). Unlike a rate-limit patch, explicit ``null`` is a
    legitimate, meaningful value here -- it is how an operator retracts a
    previous override -- so it is never rejected.

    At least one capability field MUST be present in the body; a patch
    setting only ``confirm`` / ``reason`` with no capability change is
    rejected with HTTP 422.

    Attributes:
        confirm: Deliberate-action confirmation, required (alongside a
            non-blank ``reason``) only when the patch sets
            ``supports_vision=True`` on the model currently bound to
            ``security.vision_verify_model``: that specific transition can
            make the vision-verify fail-closed gate silently pass a model
            that cannot actually see, so it is governed on the same terms
            as a security-weakening settings write. Every other capability
            field is unguarded.
        reason: Non-blank justification required alongside ``confirm=True``
            for the same governed transition.
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
    confirm: bool = Field(default=False)
    reason: str = Field(default="")

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        """Reject a patch touching no capability field.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If no capability field was set at all.
        """
        set_fields = set(self.model_dump(exclude_unset=True)) - _GOVERNANCE_FIELDS
        if not set_fields:
            msg = (
                "capability-override patch must set at least one capability "
                "field; an empty patch has no effect"
            )
            raise ValueError(msg)
        return self

    def capability_fields(self) -> dict[str, object]:
        """The explicitly-set capability fields, excluding the governance pair.

        Returns:
            A mapping of capability field name to its new value (``None``
            for an explicit clear), never including ``confirm`` / ``reason``.
        """
        return {
            key: value
            for key, value in self.model_dump(exclude_unset=True).items()
            if key not in _GOVERNANCE_FIELDS
        }
