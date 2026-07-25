# module-kind: code
"""DTOs for the setup wizard's per-feature model recommendations.

Split from the wizard's general DTO module because these carry the
provider-binding contract: every per-feature model setting is a
``SettingType.MODEL_REF``, so a recommendation and each candidate travel as a
provider-bound pair rather than a bare model id.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


class SetupModelCandidate(BaseModel):
    """One selectable provider-bound model for a wizard model picker.

    Carries the canonical serialized ``MODEL_REF`` alongside its parts, so the
    dashboard writes back exactly the value the settings validator accepts
    without re-implementing the serializer client-side. Two providers serving
    the same model id stay distinguishable because the pair, not the bare id,
    identifies a candidate.

    Attributes:
        provider: Provider connection name that serves the model.
        model_id: Model id within that provider.
        ref: Canonical ``{"provider", "model_id"}`` JSON for a settings write.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr
    model_id: NotBlankStr
    ref: NotBlankStr


class SetupModelRecommendationsResponse(BaseModel):
    """Wizard model-selection recommendations + candidate lists.

    Lets the setup wizard prefill the coordinator's decomposition model and the
    memory embedding model with sensible defaults (best-ranked / most-senior
    catalogue model) while leaving the operator free to override either from
    the full configured catalogue.

    Every per-feature model setting is a ``SettingType.MODEL_REF``, which
    rejects a provider-less value at write time, so both the recommendation and
    the candidate list for those pickers are provider-bound: the
    ``*_recommended`` fields carry a serialized ref matching one candidate's
    ``ref``. The embedding pair is the sole exception -- ``memory.embedder_model``
    is a plain ``SettingType.STRING``, so it stays a bare model id on both sides.

    Attributes:
        decomposition_recommended: Suggested decomposition model ref, if any.
        decomposition_candidates: All catalogue models selectable for it.
        embedding_recommended: Suggested embedding model id (bare), if any.
        embedding_recommended_dims: Output dims for the suggested embedder.
        embedding_candidates: Catalogue model ids that are embedding-capable.
        research_recommended: Suggested research model ref (a capable
            model), if any. Research uses its own model, not the
            decomposition model.
        cos_recommended: Suggested Chief-of-Staff chat model ref (a
            cheaper model for frequent conversational turns), if any.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    decomposition_recommended: NotBlankStr | None = None
    decomposition_candidates: tuple[SetupModelCandidate, ...] = ()
    embedding_recommended: NotBlankStr | None = None
    embedding_recommended_dims: int | None = Field(default=None, ge=1)
    embedding_candidates: tuple[str, ...] = ()
    research_recommended: NotBlankStr | None = None
    cos_recommended: NotBlankStr | None = None
    propose_recommended: NotBlankStr | None = None
    routing_recommended: NotBlankStr | None = None
    narrative_recommended: NotBlankStr | None = None
    charter_recommended: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_embedding_pairing(self) -> Self:
        """Dims and a recommended embedder must be present together.

        Returns:
            The validated instance (``self``), unchanged.

        Raises:
            ValueError: When exactly one of ``embedding_recommended`` and
                ``embedding_recommended_dims`` is set (the wizard cannot
                prefill a dimension with no model, or vice versa).
        """
        if (self.embedding_recommended is None) != (
            self.embedding_recommended_dims is None
        ):
            msg = (
                "embedding_recommended and embedding_recommended_dims must be "
                "set together or both omitted"
            )
            raise ValueError(msg)
        return self
