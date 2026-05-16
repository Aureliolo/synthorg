"""Model-pin metadata for prompt classes.

Every prompt class that wraps an LLM call exposes a
:class:`ModelPinMetadata` instance via a ``metadata`` property. The
metadata captures:

- ``prompt_class_id``: stable identifier for the prompt class (used
  by the golden-eval pipeline to locate fixtures and by audit
  dashboards to slice cost / latency by prompt purpose).
- ``model``: pinned model identifier the class was validated against.
  Changing the model requires a metadata bump plus eval refresh.
- ``model_version_pinned_at``: when the pin was last validated.
  Operators reading the dashboard see this timestamp and know whether
  the prompt has been re-evaluated against the live provider recently.
- ``temperature`` and ``top_p``: deterministic sampling parameters
  for the call. Pinned so eval results stay reproducible.

The model is frozen and ``extra="forbid"`` so an accidental rename of
a field surfaces at construction time rather than as a silent
field-name drift in dashboards.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- pydantic field annotation


class ModelPinMetadata(BaseModel):
    """Pinned-model metadata for a prompt class."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_class_id: NotBlankStr = Field(
        description="Stable identifier for the prompt class",
    )
    model: NotBlankStr = Field(description="Pinned model identifier")
    model_version_pinned_at: AwareDatetime = Field(
        description="Last validation timestamp for the pin",
    )
    temperature: float = Field(ge=0.0, le=2.0, description="Sampling temperature")
    top_p: float = Field(ge=0.0, le=1.0, description="Nucleus-sampling top-p")
