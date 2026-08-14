# module-kind: service
"""Compose the effective capability of every configured model.

Three layers, strongest first:

1. **An operator or accepted-LLM override.** Somebody decided; nothing
   outranks that.
2. **Published evidence.** A measurement of the model itself, from a source
   the operator enabled.
3. **The heuristic classifier.** Size, price and a vendor's own usage band:
   proxies, and the reason this precedence exists at all. A proxy is what
   let an older, larger, dearer model outrank a newer one that benchmarked
   above it.

Only overrides are stored. Both lower layers are recomputed on every read,
so neither can go stale against the models actually configured, and a
source that stops answering degrades to the layer below it rather than
freezing the whole map.
"""

from collections.abc import Mapping, Sequence
from typing import Final, Protocol, runtime_checkable

from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import CapabilityLevel, capability_rank
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_CAPABILITY_CLASSIFIED,
    PROVIDER_CAPABILITY_EVIDENCE_APPLIED,
    PROVIDER_CAPABILITY_EVIDENCE_DISAGREED,
    PROVIDER_CAPABILITY_OVERRIDDEN,
    PROVIDER_CAPABILITY_SOURCE_UNMATCHED,
)
from synthorg.providers.capability_assignment.classifier import (
    HeuristicCapabilityClassifier,
    ModelCapabilityClassifier,
)
from synthorg.providers.capability_assignment.models import (
    CapabilityAssignment,
    CapabilityOverride,
    CapabilityOverrideMap,
    OverrideProvenance,
)
from synthorg.providers.capability_sources.grading import (
    CapabilityThresholds,
    EvidenceGrade,
    grade_sources,
    resolve_evidence_grade,
)
from synthorg.providers.capability_sources.ingest import scores_for_enabled
from synthorg.providers.capability_sources.matching import (
    ConfiguredModelIndex,
    MatchReport,
    match_identifiers,
)
from synthorg.providers.capability_sources.models import CapabilityScore

logger = get_logger(__name__)


def _weaker(candidate: EvidenceGrade, current: EvidenceGrade) -> bool:
    """Whether *candidate* grades lower than *current*.

    Ordered exactly as ``resolve_evidence_grade`` orders its own
    candidates, so the two places that settle competing grades agree.

    Returns:
        ``True`` when *candidate* should displace *current*.
    """
    return (capability_rank(candidate.capability), candidate.percentile) < (
        capability_rank(current.capability),
        current.percentile,
    )


#: An override is authoritative over every computed layer, so it carries
#: full trust.
_OVERRIDE_CONFIDENCE: Final[float] = 1.0

#: Trust in an evidence-led rung. Above every heuristic signal because it
#: measures capability rather than standing in for it, and below an
#: override because an operator who disagrees has the last word. The
#: strength of the particular measurement is carried by the reason string
#: (source, standing and cohort size) rather than folded into one number
#: that would hide which of them was weak.
_EVIDENCE_CONFIDENCE: Final[float] = 0.95

#: Distinct rungs across sources that constitute a disagreement worth
#: reporting. One rung is agreement however many sources produced it.
_DISAGREEMENT_MIN_RUNGS: Final[int] = 2


@runtime_checkable
class CapabilityOverrideStore(Protocol):
    """Loads and persists the capability-override envelope."""

    async def load(self) -> CapabilityOverrideMap:
        """Return the persisted override map (empty when none is stored)."""
        ...

    async def save(self, overrides: CapabilityOverrideMap) -> None:
        """Persist *overrides*."""
        ...


@runtime_checkable
class CapabilityScoreReader(Protocol):
    """Reads the persisted per-axis scores every enabled source produced."""

    async def all_scores(self) -> Sequence[CapabilityScore]:
        """Return every persisted score row."""
        ...


class CapabilityAssignmentService:
    """Builds the effective per-model capability map and applies overrides.

    Args:
        store: Persistence for the override envelope.
        classifier: The heuristic classifier for the bottom layer.
        scores: Reader for published evidence. Left unwired, the service
            composes overrides over the heuristic exactly as before: an
            installation with no enabled source is not a degraded state.
        thresholds: Where the evidence layer's rung boundaries sit.
        enabled_sources: Which sources may contribute. ``None`` admits
            every source that has rows. A disabled source keeps its rows
            rather than having them deleted, so that switching it back on
            restores its evidence without a re-fetch; that is why the
            filter has to happen here on read.
        clock: Time source stamped onto written overrides, and the
            reference point for the evidence recency cut.
    """

    __slots__ = (
        "_classifier",
        "_clock",
        "_enabled_sources",
        "_scores",
        "_store",
        "_thresholds",
    )

    def __init__(
        self,
        *,
        store: CapabilityOverrideStore,
        classifier: ModelCapabilityClassifier | None = None,
        scores: CapabilityScoreReader | None = None,
        thresholds: CapabilityThresholds | None = None,
        enabled_sources: Sequence[str] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._classifier = classifier or HeuristicCapabilityClassifier()
        self._scores = scores
        self._thresholds = thresholds
        self._enabled_sources = (
            None if enabled_sources is None else frozenset(enabled_sources)
        )
        self._clock = clock or SystemClock()

    async def effective_assignments(
        self,
        providers: Mapping[str, ProviderConfig],
    ) -> tuple[CapabilityAssignment, ...]:
        """Return the effective capability assignment for every configured model.

        Returns:
            One :class:`CapabilityAssignment` per model across all providers, ordered
            by ``(provider, model_id)``.
        """
        override_index = await self._override_index()
        evidence = await self._evidence_index(providers)
        return tuple(
            self._assign(
                provider_name,
                model,
                override=override_index.get((provider_name, model.id)),
                grade=evidence.get((provider_name, model.id)),
            )
            for provider_name in sorted(providers)
            for model in sorted(providers[provider_name].models, key=lambda m: m.id)
        )

    def _assign(
        self,
        provider_name: str,
        model: ProviderModelConfig,
        *,
        override: CapabilityOverride | None,
        grade: EvidenceGrade | None,
    ) -> CapabilityAssignment:
        """Settle one model's rung through the precedence chain.

        Returns:
            The winning assignment, carrying the provenance of whichever
            layer produced it.
        """
        if override is not None:
            return CapabilityAssignment(
                provider=provider_name,
                model_id=model.id,
                capability=override.capability,
                provenance=override.provenance,
                confidence=_OVERRIDE_CONFIDENCE,
                reason=override.reason,
            )
        if grade is not None:
            return CapabilityAssignment(
                provider=provider_name,
                model_id=model.id,
                capability=grade.capability,
                provenance="evidence",
                confidence=_EVIDENCE_CONFIDENCE,
                reason=(
                    f"{grade.source_label} ranked it above "
                    f"{grade.percentile:.0%} of {grade.cohort_size} models "
                    f"on {grade.deciding_axis}, its weakest of "
                    f"{', '.join(grade.axes_used)}, measured "
                    f"{grade.as_of.date().isoformat()}"
                ),
            )
        classification = self._classifier.classify(model)
        return CapabilityAssignment(
            provider=provider_name,
            model_id=model.id,
            capability=classification.capability,
            provenance="heuristic",
            confidence=classification.confidence,
            reason=classification.reason,
        )

    async def _evidence_index(
        self,
        providers: Mapping[str, ProviderConfig],
    ) -> dict[tuple[str, str], EvidenceGrade]:
        """Grade the configured models every enabled source measured.

        Returns:
            The evidence-led rung per ``(provider, model_id)``, empty when
            no reader is wired or nothing matched.
        """
        if self._scores is None or self._thresholds is None:
            return {}
        rows = await self._scores.all_scores()
        if self._enabled_sources is not None:
            rows = list(scores_for_enabled(rows, tuple(self._enabled_sources)))
        if not rows:
            return {}
        grades = grade_sources(
            rows,
            thresholds=self._thresholds,
            now=self._clock.now(),
        )
        index = ConfiguredModelIndex(
            (provider, model.id)
            for provider, config in providers.items()
            for model in config.models
        )
        identifiers = sorted({identifier for _, identifier in grades})
        resolved, report = match_identifiers(index, identifiers)

        applied: dict[tuple[str, str], EvidenceGrade] = {}
        for identifier, pairs in resolved.items():
            grade = resolve_evidence_grade(grades, model_identifier=identifier)
            if grade is None:
                continue
            self._report_disagreement(grades, identifier, taken=grade)
            for pair in pairs:
                # Two identifiers can name one configured pair: a feed
                # routinely publishes both ``vendor/model-y`` and ``model-y``,
                # and the matcher strips the routing prefix, so both land
                # here. Grading is keyed by identifier, so nothing above has
                # compared them. Assigning would let iteration order pick the
                # rung; the lowest wins, exactly as it does across sources.
                current = applied.get(pair)
                if current is None or _weaker(grade, current):
                    applied[pair] = grade
        self._log_evidence(report, applied_count=len(applied))
        return applied

    def _report_disagreement(
        self,
        grades: Mapping[tuple[str, str], EvidenceGrade],
        identifier: str,
        *,
        taken: EvidenceGrade,
    ) -> None:
        """Surface two sources landing one model on different rungs."""
        rungs = {
            str(g.source_label): g.capability
            for (_, ident), g in grades.items()
            if ident == identifier
        }
        if len(set(rungs.values())) < _DISAGREEMENT_MIN_RUNGS:
            return
        logger.info(
            PROVIDER_CAPABILITY_EVIDENCE_DISAGREED,
            model_identifier=identifier,
            rungs_by_source=rungs,
            taken=taken.capability,
            taken_from=str(taken.source_label),
        )

    def _log_evidence(self, report: MatchReport, *, applied_count: int) -> None:
        """Record what the evidence pass could and could not place."""
        logger.info(
            PROVIDER_CAPABILITY_EVIDENCE_APPLIED,
            matched_identifiers=report.matched_identifiers,
            matched_models=report.matched_models,
            applied_count=applied_count,
        )
        if report.unmatched_identifiers:
            logger.info(
                PROVIDER_CAPABILITY_SOURCE_UNMATCHED,
                unmatched_identifiers=report.unmatched_identifiers,
                matched_identifiers=report.matched_identifiers,
            )

    async def capability_lookup(
        self,
        providers: Mapping[str, ProviderConfig],
    ) -> dict[tuple[str, str], CapabilityLevel]:
        """Return a ``(provider, model_id) -> rung`` map for the resolver.

        Returns:
            The effective rung of each configured model, keyed by its
            ``(provider, model_id)`` pair.
        """
        assignments = await self.effective_assignments(providers)
        lookup: dict[tuple[str, str], CapabilityLevel] = {
            (str(a.provider), str(a.model_id)): a.capability for a in assignments
        }
        logger.info(
            PROVIDER_CAPABILITY_CLASSIFIED,
            model_count=len(lookup),
            provider_count=len(providers),
        )
        return lookup

    async def set_override(
        self,
        *,
        provider: str,
        model_id: str,
        capability: CapabilityLevel,
        provenance: OverrideProvenance,
        reason: str,
    ) -> CapabilityOverride:
        """Persist an override for one model, replacing any prior override.

        Returns:
            The written :class:`CapabilityOverride`.
        """
        override = CapabilityOverride(
            provider=provider,
            model_id=model_id,
            capability=capability,
            provenance=provenance,
            reason=reason,
            updated_at=self._clock.now(),
        )
        current = await self._store.load()
        kept = tuple(
            o
            for o in current.overrides
            if (o.provider, o.model_id) != (provider, model_id)
        )
        await self._store.save(
            current.model_copy(update={"overrides": (*kept, override)}),
        )
        logger.info(
            PROVIDER_CAPABILITY_OVERRIDDEN,
            provider=provider,
            model_id=model_id,
            capability=capability,
            provenance=provenance,
            action="set",
        )
        return override

    async def clear_override(self, *, provider: str, model_id: str) -> bool:
        """Remove the override for one model, reverting it to the heuristic.

        Returns:
            ``True`` when an override was removed, ``False`` when none existed.
        """
        current = await self._store.load()
        kept = tuple(
            o
            for o in current.overrides
            if (o.provider, o.model_id) != (provider, model_id)
        )
        if len(kept) == len(current.overrides):
            return False
        await self._store.save(current.model_copy(update={"overrides": kept}))
        logger.info(
            PROVIDER_CAPABILITY_OVERRIDDEN,
            provider=provider,
            model_id=model_id,
            action="clear",
        )
        return True

    async def _override_index(
        self,
    ) -> dict[tuple[str, str], CapabilityOverride]:
        """Return the persisted overrides keyed by ``(provider, model_id)``.

        Returns:
            The override index; empty when nothing is persisted.
        """
        stored = await self._store.load()
        return {(o.provider, o.model_id): o for o in stored.overrides}


__all__ = [
    "CapabilityAssignmentService",
    "CapabilityOverrideStore",
    "CapabilityScoreReader",
]
