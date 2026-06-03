# module-kind: service
"""Substrate-backed grounding checker.

Resolves every assertive factual claim in a deliverable against the
project-scoped knowledge corpus using LLM claim-extraction plus semantic
entailment, and flags the unsupported ones as
``source="knowledge_substrate"`` :class:`UngroundedClaim` entries across
the full ``[0,1]`` confidence range. Unlike the heuristic stub, these
claims escalate (via :func:`substrate_severity_for_confidence`) up to the
HIGH cap, so a substrate finding can BLOCK a deliverable and reroute it to
rework.

Precision is the contract. Three guards keep grounded work from being
wrongly blocked:

* The corpus-empty invariant: a claim whose search returns no hits is
  never flagged. An unpopulated corpus degrades, it does not mass-block.
* Entailment is biased toward "supported / uncertain"; only an explicit
  "unsupported" verdict at or above :data:`SUBSTRATE_DROP_FLOOR` confidence
  becomes a claim.
* When the substrate is not wired, or extraction fails, the checker
  delegates to the deterministic :class:`HeuristicGroundingChecker` rather
  than emitting substrate-grade (blocking) findings on no evidence.
"""

import asyncio
from typing import Final

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.red_team import (
    RED_TEAM_GROUNDING_CLAIM_UNSUPPORTED,
    RED_TEAM_GROUNDING_CORPUS_EMPTY,
    RED_TEAM_GROUNDING_ENTAILMENT_FAILED,
    RED_TEAM_GROUNDING_EXTRACTION_FAILED,
    RED_TEAM_GROUNDING_SEARCH_FAILED,
    RED_TEAM_GROUNDING_SUBSTRATE_DEGRADED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
)
from synthorg.security.redteam.grounding._llm import (
    ENTAILMENT_MAX_TOKENS,
    EXTRACT_CLAIMS_TOOL,
    EXTRACTION_MAX_TOKENS,
    GROUNDING_VERDICT_TOOL,
    LLM_TEMPERATURE,
    build_entailment_messages,
    build_extraction_messages,
    parse_extracted_claims,
    parse_grounding_verdict,
)
from synthorg.security.redteam.grounding.heuristic import HeuristicGroundingChecker
from synthorg.security.redteam.grounding.models import UngroundedClaim
from synthorg.security.redteam.grounding.protocol import GroundingChecker
from synthorg.security.redteam.grounding.resolver import (
    GroundingSubstrateContext,
    GroundingSubstrateResolver,
)
from synthorg.security.redteam.routing import SUBSTRATE_DROP_FLOOR

logger = get_logger(__name__)

_GROUNDING_AGENT_ID: Final[str] = "system:red_team:grounding"
"""Synthetic attribution id for the checker's extraction / entailment calls."""

_DEFAULT_SEARCH_LIMIT: Final[int] = 5
"""Default top-k chunks retrieved per claim for entailment."""

_UNGROUNDED_REASON: Final[str] = "not supported by the project knowledge corpus"
"""Static rationale; the LLM's free-text reason is untrusted and not surfaced."""

_UNSUPPORTED_LABEL: Final[str] = "unsupported"


class KnowledgeSubstrateGroundingChecker:
    """Corpus-grounded ungrounded-claim detector.

    Implements :class:`synthorg.security.redteam.grounding.protocol.GroundingChecker`.

    Args:
        resolver: Resolves the live knowledge service + provider at check
            time (the checker is built before the substrate wires).
        fallback: Checker used when the substrate is unavailable or
            extraction fails; defaults to a fresh
            :class:`HeuristicGroundingChecker`.
        search_limit: Top-k corpus chunks retrieved per claim.
    """

    def __init__(
        self,
        *,
        resolver: GroundingSubstrateResolver,
        fallback: GroundingChecker | None = None,
        search_limit: int = _DEFAULT_SEARCH_LIMIT,
    ) -> None:
        self._resolver = resolver
        self._fallback: GroundingChecker = fallback or HeuristicGroundingChecker()
        self._search_limit = search_limit

    async def check(
        self,
        *,
        deliverable_content: NotBlankStr,
        execution_id: NotBlankStr,
        project_id: NotBlankStr | None = None,
    ) -> tuple[UngroundedClaim, ...]:
        """Flag deliverable claims unsupported by the project corpus.

        Degrades to the heuristic fallback when the substrate is not wired
        or claim-extraction fails. Returns substrate-source claims only for
        claims whose corpus search returned evidence AND whose entailment
        verdict was an at-or-above-floor "unsupported".

        Returns:
            The ungrounded claims; an empty tuple when none are found or
            when there is nothing to check.

        Raises:
            asyncio.CancelledError: Propagated when claim extraction is
                cancelled, so the awaiting parent observes it.
        """
        context = self._resolver()
        if context is None or context.knowledge_service is None:
            logger.info(
                RED_TEAM_GROUNDING_SUBSTRATE_DEGRADED,
                execution_id=execution_id,
                reason="substrate_unavailable",
            )
            return await self._delegate(deliverable_content, execution_id, project_id)

        try:
            claims = await self._extract_claims(
                context, deliverable_content, execution_id
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                RED_TEAM_GROUNDING_EXTRACTION_FAILED,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                policy="degrade_to_heuristic",
            )
            return await self._delegate(deliverable_content, execution_id, project_id)

        if not claims:
            return ()

        flagged: list[UngroundedClaim] = []
        for claim in claims:
            ungrounded = await self._evaluate_claim(
                context, claim, execution_id, project_id
            )
            if ungrounded is not None:
                flagged.append(ungrounded)
        return tuple(flagged)

    async def _delegate(
        self,
        deliverable_content: NotBlankStr,
        execution_id: NotBlankStr,
        project_id: NotBlankStr | None,
    ) -> tuple[UngroundedClaim, ...]:
        """Hand off to the heuristic fallback.

        Returns:
            The fallback checker's claims.
        """
        return await self._fallback.check(
            deliverable_content=deliverable_content,
            execution_id=execution_id,
            project_id=project_id,
        )

    async def _extract_claims(
        self,
        context: GroundingSubstrateContext,
        deliverable_content: NotBlankStr,
        execution_id: NotBlankStr,
    ) -> tuple[str, ...]:
        """Extract assertive factual claims from the deliverable.

        Returns:
            The extracted claim strings (possibly empty).
        """
        response = await self._complete(
            context,
            execution_id,
            messages=build_extraction_messages(deliverable_content),
            tools=[EXTRACT_CLAIMS_TOOL],
            max_tokens=EXTRACTION_MAX_TOKENS,
        )
        return parse_extracted_claims(response)

    async def _evaluate_claim(
        self,
        context: GroundingSubstrateContext,
        claim: str,
        execution_id: NotBlankStr,
        project_id: NotBlankStr | None,
    ) -> UngroundedClaim | None:
        """Resolve one claim against the corpus; flag it only if unsupported.

        Per-claim search or entailment failures are logged and skipped
        (fail-soft) rather than blocking the gate.

        Returns:
            An :class:`UngroundedClaim` when the claim is judged unsupported
            with at-or-above-floor confidence; ``None`` otherwise (including
            empty corpus, transient failure, or a supported / uncertain
            verdict).

        Raises:
            asyncio.CancelledError: Propagated when the corpus search or
                entailment call is cancelled.
        """
        knowledge_service = context.knowledge_service
        if knowledge_service is None:  # pragma: no cover - guarded by caller
            return None
        try:
            hits = await knowledge_service.search(
                query=NotBlankStr(claim),
                project_id=project_id,
                limit=self._search_limit,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                RED_TEAM_GROUNDING_SEARCH_FAILED,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                policy="skip_claim",
            )
            return None

        if not hits:
            logger.debug(
                RED_TEAM_GROUNDING_CORPUS_EMPTY,
                execution_id=execution_id,
            )
            return None

        try:
            response = await self._complete(
                context,
                execution_id,
                messages=build_entailment_messages(claim, hits),
                tools=[GROUNDING_VERDICT_TOOL],
                max_tokens=ENTAILMENT_MAX_TOKENS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                RED_TEAM_GROUNDING_ENTAILMENT_FAILED,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                policy="skip_claim",
            )
            return None

        verdict = parse_grounding_verdict(response)
        if verdict is None:
            return None
        label, confidence = verdict
        if label != _UNSUPPORTED_LABEL or confidence < SUBSTRATE_DROP_FLOOR:
            return None

        logger.info(
            RED_TEAM_GROUNDING_CLAIM_UNSUPPORTED,
            execution_id=execution_id,
            confidence=confidence,
        )
        return UngroundedClaim(
            excerpt=NotBlankStr(claim),
            reason=NotBlankStr(_UNGROUNDED_REASON),
            confidence=confidence,
            source="knowledge_substrate",
            expected_source_kind=None,
        )

    async def _complete(
        self,
        context: GroundingSubstrateContext,
        execution_id: NotBlankStr,
        *,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        max_tokens: int,
    ) -> CompletionResponse:
        """Run one structured completion under a cost-recording scope.

        Returns:
            The provider's completion response.
        """
        async with cost_recording_scope(
            cost_tracker=context.cost_tracker,
            agent_id=NotBlankStr(_GROUNDING_AGENT_ID),
            task_id=execution_id,
            call_category=LLMCallCategory.SYSTEM,
        ):
            return await context.provider.complete(
                messages,
                context.model_id,
                tools=tools,
                config=CompletionConfig(
                    temperature=LLM_TEMPERATURE,
                    max_tokens=max_tokens,
                ),
            )
