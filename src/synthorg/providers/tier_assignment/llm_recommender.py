# module-kind: service
"""LLM-assisted model-tier recommender (opt-in).

Asks a configured LLM to recommend a routing tier (small / medium / large) for
one or more configured models, given their capability metadata. The heuristic
classifier is the default; this is the "Recommend by LLM" / "Recommend all
fresh from LLM" enhancement, surfaced to the operator as an *offer* they accept
before it becomes an override.

The recommender runs on the operator-selected ``providers.tier_classifier_model``
(resolved by the wiring). Its prompt class carries the
``system:providers:tier_classification`` purpose so the spend is cost-attributed
and the model pin is validated.
"""

import json
from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.core.types import ModelTier, NotBlankStr
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import PROVIDER_TIER_LLM_RECOMMENDED
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.structured_text import complete_text, extract_json_object
from synthorg.providers.tier_assignment.models import TierRecommendation

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You classify LLM models into a routing tier for a synthetic-organisation "
    "engine. The three tiers are: 'small' (cheap, light workers for bounded "
    "classification/triage), 'medium' (mid-capability for judgement and "
    "verification), and 'large' (the strongest models for open-ended synthesis, "
    "planning, and code). Judge each model by its capability metadata (parameter "
    "count, generation, context window, tool support) and cost. Respond ONLY "
    "with a JSON object of the form "
    '{"recommendations": [{"model_id": str, "tier": "small"|"medium"|"large", '
    '"confidence": number between 0 and 1, "rationale": str}]}. Include exactly '
    "one entry per model given, keyed by its exact model_id."
)


class _RecommendationItem(
    BaseModel
):  # lint-allow: frozen-extra-forbid -- LLM output may carry extra keys; ignore them rather than reject the whole response  # noqa: E501
    """One model's LLM tier recommendation, parsed from the response."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="ignore")

    model_id: NotBlankStr
    tier: ModelTier
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: NotBlankStr


class _RecommendationResponse(
    BaseModel
):  # lint-allow: frozen-extra-forbid -- LLM output may carry extra keys; ignore them rather than reject the whole response  # noqa: E501
    """The parsed recommender response envelope."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="ignore")

    recommendations: tuple[_RecommendationItem, ...] = Field(default=())


def _describe_model(model: ProviderModelConfig) -> str:
    """Render a compact metadata description for the prompt.

    Returns:
        A single-line description of the model's tier-relevant signals.
    """
    meta = model.metadata
    total_cost = model.cost_per_1k_input + model.cost_per_1k_output
    return (
        f"- {model.id}: params={meta.parameter_count}, "
        f"generation={meta.generation}, cost_tier={meta.cost_tier}, "
        f"max_context={model.max_context}, supports_tools={meta.supports_tools}, "
        f"cost_per_1k={total_cost:g}"
    )


class LlmTierRecommender:
    """Recommends a routing tier for configured models via an LLM.

    Args:
        provider: The completion provider serving the classifier model.
        model_id: The concrete classifier model id.
        cost_tracker: Optional sink for the recommender's own spend.
    """

    _PURPOSE_ID: ClassVar[PromptPurposeId] = (
        PromptPurposeId.PROVIDERS_TIER_CLASSIFICATION
    )

    __slots__ = ("_cost_tracker", "_model_id", "_provider")

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model_id: str,
        cost_tracker: CostTrackerProtocol | None = None,
    ) -> None:
        self._provider = provider
        self._model_id = model_id
        self._cost_tracker = cost_tracker

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for this prompt class."""
        return pin_for(self._PURPOSE_ID)

    async def recommend(
        self,
        provider_name: str,
        models: Sequence[ProviderModelConfig],
    ) -> tuple[TierRecommendation, ...]:
        """Return an LLM tier recommendation for each of *models*.

        The recommendations are offers, not overrides: the caller decides
        whether to apply them.

        Returns:
            One :class:`TierRecommendation` per model the LLM classified; a
            model the response omits or malforms is skipped (logged), never
            fabricated.
        """
        if not models:
            return ()
        user_prompt = "Classify these models:\n" + "\n".join(
            _describe_model(m) for m in models
        )
        content, _cost = await complete_text(
            self._provider,
            self._model_id,
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            task_id=NotBlankStr(
                f"system:providers:tier_classification:{provider_name}"
            ),
            purpose=self.metadata.prompt_class_id,
            cost_tracker=self._cost_tracker,
        )
        parsed = self._parse(content)
        by_id = {item.model_id: item for item in parsed.recommendations}
        recommendations: list[TierRecommendation] = []
        for model in models:
            item = by_id.get(model.id)
            if item is None:
                continue
            recommendations.append(
                TierRecommendation(
                    provider=provider_name,
                    model_id=model.id,
                    tier=item.tier,
                    confidence=item.confidence,
                    rationale=item.rationale,
                ),
            )
        logger.info(
            PROVIDER_TIER_LLM_RECOMMENDED,
            provider=provider_name,
            requested=len(models),
            recommended=len(recommendations),
        )
        return tuple(recommendations)

    def _parse(self, content: str) -> _RecommendationResponse:
        """Parse the recommender response, degrading to empty on malformed output.

        Returns:
            The parsed response, or an empty envelope when the model returned
            no decodable JSON object matching the schema.
        """
        try:
            payload = json.loads(extract_json_object(content))
            return _RecommendationResponse.model_validate(payload)
        except (ValueError, ValidationError) as exc:
            logger.warning(
                PROVIDER_TIER_LLM_RECOMMENDED,
                reason="unparseable_response",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _RecommendationResponse()


__all__ = ["LlmTierRecommender"]
