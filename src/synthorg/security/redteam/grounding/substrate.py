# module-kind: service
"""Substrate-backed grounding checker.

Resolves every assertive factual claim in a deliverable against the
project-scoped knowledge corpus using LLM claim-extraction plus semantic
entailment, and flags the unsupported ones as
``source="knowledge_substrate"`` :class:`UngroundedClaim` entries across
the ``[SUBSTRATE_DROP_FLOOR, 1.0]`` confidence band (claims below the drop
floor are not emitted). Unlike the heuristic checker, these claims escalate
(via :func:`substrate_severity_for_confidence`) up to the HIGH cap, so a
substrate finding can BLOCK a deliverable and reroute it to rework.

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
from typing import ClassVar, Final

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.models import KnowledgeHit
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.red_team import (
    RED_TEAM_GROUNDING_CLAIM_UNSUPPORTED,
    RED_TEAM_GROUNDING_CORPUS_EMPTY,
    RED_TEAM_GROUNDING_DELIVERABLE_TRUNCATED,
    RED_TEAM_GROUNDING_ENTAILMENT_FAILED,
    RED_TEAM_GROUNDING_EXTRACTION_EMPTY,
    RED_TEAM_GROUNDING_EXTRACTION_FAILED,
    RED_TEAM_GROUNDING_SEARCH_FAILED,
    RED_TEAM_GROUNDING_SUBSTRATE_DEGRADED,
    RED_TEAM_GROUNDING_VERDICT_UNPARSEABLE,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
)
from synthorg.security.redteam.grounding._llm import (
    ENTAILMENT_CONFIG,
    EXTRACT_CLAIMS_TOOL,
    EXTRACTION_CONFIG,
    GROUNDING_VERDICT_TOOL,
    MAX_DELIVERABLE_CHARS,
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

    Raises:
        ValueError: If ``search_limit`` is not positive (a non-positive
            limit silently neutralises the checker, since every claim's
            corpus search returns no hits and is skipped).
    """

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.RED_TEAM_GROUNDING
    _ENTAILMENT_PURPOSE_ID: ClassVar[PromptPurposeId] = (
        PromptPurposeId.RED_TEAM_GROUNDING_ENTAILMENT
    )

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for the claim-extraction prompt class."""
        return pin_for(self._PURPOSE_ID)

    @property
    def entailment_metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for the claim-entailment prompt class.

        Extraction and entailment send different tool contracts and sampling
        configs, so they are attributed to distinct prompt classes.
        """
        return pin_for(self._ENTAILMENT_PURPOSE_ID)

    def __init__(
        self,
        *,
        resolver: GroundingSubstrateResolver,
        fallback: GroundingChecker | None = None,
        search_limit: int = _DEFAULT_SEARCH_LIMIT,
    ) -> None:
        if search_limit <= 0:
            msg = f"search_limit must be positive; got {search_limit}"
            raise ValueError(msg)
        self._resolver = resolver
        self._fallback: GroundingChecker = fallback or HeuristicGroundingChecker()
        self._search_limit = search_limit

    async def check(
        self,
        *,
        deliverable_content: NotBlankStr,
        execution_id: NotBlankStr,
        project_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
    ) -> tuple[UngroundedClaim, ...]:
        """Flag deliverable claims unsupported by the project corpus.

        Degrades to the heuristic fallback when the substrate is not wired
        or claim-extraction fails. Returns substrate-source claims only for
        claims whose corpus search returned evidence AND whose entailment
        verdict was an at-or-above-floor "unsupported". Extracted claims are
        evaluated concurrently (bounded by MAX_CLAIMS), preserving the
        deterministic order of the extracted list in the result.

        Returns:
            The ungrounded claims; an empty tuple when none are found or
            when there is nothing to check.

        Raises:
            asyncio.CancelledError: Propagated when claim extraction, corpus
                search, or entailment is cancelled, so the awaiting parent
                observes it.
        """
        context = self._resolve_context(execution_id)
        if context is None or context.knowledge_service is None:
            logger.info(
                RED_TEAM_GROUNDING_SUBSTRATE_DEGRADED,
                execution_id=execution_id,
                reason="no_provider_registered"
                if context is None
                else "knowledge_service_not_wired",
            )
            return await self._delegate(deliverable_content, execution_id, project_id)
        return await self._check_with_substrate(
            context, deliverable_content, execution_id, project_id, task_id
        )

    async def _check_with_substrate(
        self,
        context: GroundingSubstrateContext,
        deliverable_content: NotBlankStr,
        execution_id: NotBlankStr,
        project_id: NotBlankStr | None,
        task_id: NotBlankStr | None,
    ) -> tuple[UngroundedClaim, ...]:
        """Extract claims and evaluate each against the wired corpus.

        Degrades to the heuristic fallback when claim-extraction fails.

        Returns:
            The ungrounded claims; an empty tuple when extraction yields none.

        Raises:
            asyncio.CancelledError: Propagated when extraction, corpus search,
                or entailment is cancelled.
        """
        try:
            claims = await self._extract_claims(
                context, deliverable_content, execution_id, task_id
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
            logger.debug(
                RED_TEAM_GROUNDING_EXTRACTION_EMPTY,
                execution_id=execution_id,
            )
            return ()
        return await self._evaluate_claims_concurrently(
            context, claims, execution_id, project_id, task_id
        )

    async def _evaluate_claims_concurrently(
        self,
        context: GroundingSubstrateContext,
        claims: tuple[str, ...],
        execution_id: NotBlankStr,
        project_id: NotBlankStr | None,
        task_id: NotBlankStr | None,
    ) -> tuple[UngroundedClaim, ...]:
        """Evaluate every extracted claim concurrently, order-preserving.

        Fans the per-claim corpus search + entailment round-trips out
        concurrently: each claim costs two LLM calls, so a serial loop makes
        worst-case latency scale linearly with claim count (up to MAX_CLAIMS)
        and starves later claims under timeout pressure. ``_evaluate_claim``
        is fail-soft (it swallows everything but critical errors and
        cancellation), so a single bad claim never aborts the group. Results
        are slotted by index to keep output order deterministic regardless of
        completion order.

        Returns:
            The flagged claims in extracted order.

        Raises:
            asyncio.CancelledError: Propagated from a cancelled child task.
        """
        flagged: list[UngroundedClaim | None] = [None] * len(claims)

        async def _evaluate_into(index: int, claim: str) -> None:
            flagged[index] = await self._evaluate_claim(
                context, claim, execution_id, project_id, task_id
            )

        async with asyncio.TaskGroup() as task_group:
            for index, claim in enumerate(claims):
                _ = task_group.create_task(_evaluate_into(index, claim))
        return tuple(claim for claim in flagged if claim is not None)

    def _resolve_context(
        self, execution_id: NotBlankStr
    ) -> GroundingSubstrateContext | None:
        """Resolve the live substrate context, treating a raise as unavailable.

        The resolver reads live application state (provider registry,
        knowledge service); a mid-flight hot-swap could surface a transient
        error. Treating that as ``None`` keeps the degrade-to-heuristic path
        symmetric with extraction failure rather than crashing the gate.

        Returns:
            The resolved context, or ``None`` when no provider is registered
            or the resolver raised.
        """
        try:
            return self._resolver()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                RED_TEAM_GROUNDING_SUBSTRATE_DEGRADED,
                execution_id=execution_id,
                reason="resolver_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

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
        task_id: NotBlankStr | None,
    ) -> tuple[str, ...]:
        """Extract assertive factual claims from the deliverable.

        Returns:
            The extracted claim strings (possibly empty).
        """
        if len(deliverable_content) > MAX_DELIVERABLE_CHARS:
            logger.debug(
                RED_TEAM_GROUNDING_DELIVERABLE_TRUNCATED,
                execution_id=execution_id,
                original_length=len(deliverable_content),
                cap=MAX_DELIVERABLE_CHARS,
            )
        response = await self._complete_extraction(
            context,
            task_id,
            messages=build_extraction_messages(deliverable_content),
        )
        return parse_extracted_claims(response)

    async def _evaluate_claim(
        self,
        context: GroundingSubstrateContext,
        claim: str,
        execution_id: NotBlankStr,
        project_id: NotBlankStr | None,
        task_id: NotBlankStr | None,
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
        hits = await self._search_corpus(context, claim, execution_id, project_id)
        if not hits:
            return None
        return await self._entail_claim(context, claim, hits, execution_id, task_id)

    async def _search_corpus(
        self,
        context: GroundingSubstrateContext,
        claim: str,
        execution_id: NotBlankStr,
        project_id: NotBlankStr | None,
    ) -> tuple[KnowledgeHit, ...]:
        """Retrieve corpus evidence for one claim (fail-soft).

        Returns:
            The retrieved hits; an empty tuple when the corpus is empty, the
            knowledge service is unwired, or the search fails transiently.

        Raises:
            asyncio.CancelledError: Propagated when the search is cancelled.
        """
        knowledge_service = context.knowledge_service
        if knowledge_service is None:  # pragma: no cover - guarded by caller
            return ()
        try:
            hits = await knowledge_service.search(
                query=NotBlankStr(claim),
                project_id=project_id,
                limit=self._search_limit,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                RED_TEAM_GROUNDING_SEARCH_FAILED,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                policy="skip_claim",
            )
            return ()
        if not hits:
            logger.debug(
                RED_TEAM_GROUNDING_CORPUS_EMPTY,
                execution_id=execution_id,
            )
        return hits

    async def _entail_claim(
        self,
        context: GroundingSubstrateContext,
        claim: str,
        hits: tuple[KnowledgeHit, ...],
        execution_id: NotBlankStr,
        task_id: NotBlankStr | None,
    ) -> UngroundedClaim | None:
        """Judge one claim against its evidence; flag it only if unsupported.

        Returns:
            An :class:`UngroundedClaim` when the verdict is an at-or-above-floor
            "unsupported"; ``None`` otherwise (transient failure, an unparseable
            verdict, or a supported / uncertain verdict).

        Raises:
            asyncio.CancelledError: Propagated when the entailment call is
                cancelled.
        """
        try:
            response = await self._complete_entailment(
                context,
                task_id,
                messages=build_entailment_messages(claim, hits),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
            logger.debug(
                RED_TEAM_GROUNDING_VERDICT_UNPARSEABLE,
                execution_id=execution_id,
            )
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

    async def _complete_extraction(
        self,
        context: GroundingSubstrateContext,
        task_id: NotBlankStr | None,
        *,
        messages: list[ChatMessage],
    ) -> CompletionResponse:
        """Run the claim-extraction completion under a cost-recording scope.

        Returns:
            The provider's completion response.
        """
        return await self._run_completion(
            context,
            task_id,
            messages=messages,
            tools=[EXTRACT_CLAIMS_TOOL],
            config=EXTRACTION_CONFIG,
            purpose=self.metadata.prompt_class_id,
        )

    async def _complete_entailment(
        self,
        context: GroundingSubstrateContext,
        task_id: NotBlankStr | None,
        *,
        messages: list[ChatMessage],
    ) -> CompletionResponse:
        """Run the per-claim entailment completion under a cost-recording scope.

        Returns:
            The provider's completion response.
        """
        return await self._run_completion(
            context,
            task_id,
            messages=messages,
            tools=[GROUNDING_VERDICT_TOOL],
            config=ENTAILMENT_CONFIG,
            purpose=self.entailment_metadata.prompt_class_id,
        )

    async def _run_completion(
        self,
        context: GroundingSubstrateContext,
        task_id: NotBlankStr | None,
        *,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        config: CompletionConfig,
        purpose: PromptPurposeId,
    ) -> CompletionResponse:
        """Run one structured completion under a cost-recording scope.

        ``purpose`` attributes the call to a prompt class (extraction vs
        entailment) so spend and drift split per prompt. ``task_id`` owns the
        spend: grounding runs on a deliverable produced for a real task, so
        the review's cost belongs to that task rather than to no one.

        Returns:
            The provider's completion response.
        """
        async with cost_recording_scope(
            cost_tracker=context.cost_tracker,
            task_id=task_id,
            purpose=purpose,
            call_category=LLMCallCategory.SYSTEM,
        ):
            return await context.provider.complete(
                messages,
                context.model_id,
                tools=tools,
                config=config,
            )
