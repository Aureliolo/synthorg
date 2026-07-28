# module-kind: code
"""DTOs for the setup wizard's per-feature model recommendations.

Split from the wizard's general DTO module because these carry the
provider-binding contract: every per-feature model setting is a
``SettingType.MODEL_REF``, so a recommendation and each candidate travel as a
provider-bound pair rather than a bare model id.
"""

from pydantic import BaseModel, ConfigDict, computed_field

from synthorg.core.types import NotBlankStr
from synthorg.settings.model_ref import ModelRef, serialize_model_ref


class SetupModelCandidate(BaseModel):
    """One selectable provider-bound model for a wizard model picker.

    Two providers serving the same model id stay distinguishable because the
    pair, not the bare id, identifies a candidate.

    Attributes:
        provider: Provider connection name that serves the model.
        model_id: Model id within that provider.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr
    model_id: NotBlankStr

    @computed_field
    @property
    def ref(self) -> str:
        """The canonical ``{"provider", "model_id"}`` JSON for a settings write.

        Derived rather than stored so a candidate cannot carry a reference that
        disagrees with its own provider and model. Serialising it here (instead
        of leaving it to the dashboard) keeps one implementation of the
        canonical form, which the ``MODEL_REF`` validator accepts verbatim.

        Returns:
            The serialised model reference.
        """
        return serialize_model_ref(
            ModelRef(provider=self.provider, model_id=self.model_id)
        )


class SetupModelRecommendationsResponse(BaseModel):
    """Wizard model-selection recommendations + candidate lists.

    Lets the setup wizard prefill each per-feature model with a sensible
    default (best-ranked / most-senior catalogue model) while leaving the
    operator free to override any of them from the full configured catalogue.

    Every per-feature model setting is a ``SettingType.MODEL_REF``, which
    rejects a provider-less value at write time, so both the recommendation and
    the candidate list for those pickers are provider-bound: the
    ``*_recommended`` fields carry a serialised ref matching one candidate's
    ``ref``.

    Embedding carries candidates but no recommendation. The operator names the
    embedding model; nothing suggests one, because a suggestion is a decision
    made without knowing what the deployment can serve, and the last one shipped
    a width its own vector store could not index.

    Attributes:
        model_ref_candidates: The shared provider-bound catalogue every
            MODEL_REF picker selects from (coordination, research, both
            Chief-of-Staff models, concern-routing, run-narrative, charter).
        embedding_candidates: Every catalogue model, provider-bound, plus the
            built-in embedder. Unfiltered: whether a model embeds is the
            model's answer to a probe, not something a name reveals, and a
            filter would hide a working embedder this codebase has not heard
            of.
        decomposition_recommended: Suggested coordination/decomposition model
            ref, if any.
        research_recommended: Suggested research model ref, if any. Research
            uses its own model, not the decomposition model.
        cos_recommended: Suggested Chief-of-Staff chat model ref (a cheaper
            model for frequent conversational turns), if any.
        propose_recommended: Suggested request-work proposal model ref, if any.
        routing_recommended: Suggested concern-routing classifier model ref,
            if any.
        narrative_recommended: Suggested run-narrative model ref, if any.
        charter_recommended: Suggested project-charter interview model ref,
            if any.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model_ref_candidates: tuple[SetupModelCandidate, ...] = ()
    embedding_candidates: tuple[SetupModelCandidate, ...] = ()
    decomposition_recommended: NotBlankStr | None = None
    research_recommended: NotBlankStr | None = None
    cos_recommended: NotBlankStr | None = None
    propose_recommended: NotBlankStr | None = None
    routing_recommended: NotBlankStr | None = None
    narrative_recommended: NotBlankStr | None = None
    charter_recommended: NotBlankStr | None = None
