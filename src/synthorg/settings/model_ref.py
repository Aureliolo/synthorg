"""Shared model-reference value for model-assignment settings.

A ``SettingType.MODEL_REF`` setting stores a provider + model pair as
canonical JSON ``{"provider": ..., "model_id": ...}`` so the value is
unambiguous and validatable against the live provider catalogue, rather than
a bare model string whose provider is implicit (the source of a class of
"model not found on the resolved provider" runtime failures).

An empty value means "unset". A bare (non-JSON) model string is read as
model-only, with the provider left unset, so the dashboard picker can surface
the model and prompt for an explicit provider selection.
"""

import json
from typing import Final

from pydantic import BaseModel, ConfigDict, Field


class ModelRef(BaseModel):
    """A provider + model selection for a model-assignment setting."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: str = Field(
        default="",
        description="Provider connection name; empty when unset.",
    )
    model_id: str = Field(
        default="",
        description="Model id within the provider; empty when unset.",
    )

    @property
    def is_bound(self) -> bool:
        """True iff both a provider and a model are selected."""
        return bool(self.provider.strip()) and bool(self.model_id.strip())


_EMPTY: Final[ModelRef] = ModelRef()


def parse_model_ref(value: str) -> ModelRef:
    """Parse a stored MODEL_REF setting value into a :class:`ModelRef`.

    Accepts three forms: empty (unset), canonical
    ``{"provider", "model_id"}`` JSON, and a bare model string (read as
    model-only, provider empty).

    Returns:
        The parsed :class:`ModelRef`; an unset ref for an empty value.
    """
    text = value.strip()
    if not text:
        return _EMPTY
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            # Unparseable JSON: fall back to the raw value as a model-only
            # value rather than discarding the assignment entirely.
            return ModelRef(model_id=value)
        if isinstance(data, dict):
            provider = data.get("provider")
            model_id = data.get("model_id")
            return ModelRef(
                # Trimmed, unlike ``model_id``: a provider name is a registry
                # key and nothing else, so ``"  openai  "`` and ``"openai"``
                # name one connection. Left raw, every consumer would have to
                # remember to strip before its own lookup, and the one that
                # forgot would report the operator's provider unregistered.
                provider=provider.strip() if isinstance(provider, str) else "",
                model_id=model_id if isinstance(model_id, str) else "",
            )
        return ModelRef(model_id=value)
    # A bare model string (e.g. "glm-5.2") carries no provider: read it as
    # model-only so the picker can prompt for a provider selection. The raw
    # value is preserved (not the classification-stripped ``text``) so a
    # downstream structural guard still sees any untrimmed / control content.
    return ModelRef(model_id=value)


def serialize_model_ref(ref: ModelRef) -> str:
    """Serialize a :class:`ModelRef` to the canonical stored JSON string.

    Returns:
        ``{"provider": ..., "model_id": ...}`` JSON.
    """
    return json.dumps({"provider": ref.provider, "model_id": ref.model_id})
