"""Axis-split contracts for memory consolidation (ADR-0005).

Consolidation is split along two orthogonal axes:

- :class:`EntrySelector` -- *which* entries are consolidated. All
  current strategies share one selection rule, so there is one
  selector (:class:`~synthorg.memory.consolidation.selectors.HighestRelevanceSelector`).
- :class:`ConsolidationOp` -- *how* the to-remove set becomes a stored
  summary. This is where the three strategies diverge.

:class:`CompositeConsolidationStrategy
<synthorg.memory.consolidation.composite.CompositeConsolidationStrategy>`
runs a selector then an op per group, satisfying the existing
``ConsolidationStrategy`` Protocol so callers are unchanged.

The op owns the backend and performs store + delete internally with
its strategy's *exact* failure semantics (the three pre-split
strategies' delete handling is mutually incompatible -- see ADR-0005).
:class:`OpResult` is therefore the minimal cross-boundary outcome:
which summary id was created, which originals were successfully
removed, and (DualMode only) the per-entry archival-mode assignments.
The LLM prompt-cap truncation contract -- dropped entries survive for
the next pass -- is preserved *inside* ``LLMSynthesisOp``; it simply
reports the deleted subset via ``removed_ids``.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.consolidation.models import (
    ArchivalModeAssignment,
)
from synthorg.memory.models import MemoryEntry


@dataclass(frozen=True, slots=True)
class SelectionGroup:
    """One category group the selector decided to consolidate.

    Attributes:
        category: The shared memory category of the group.
        kept: The single entry retained (highest relevance, recency
            tiebreak); never deleted, never sent to the op.
        to_remove: The entries handed to the op for summarisation;
            candidates for deletion (subject to the op's
            ``represented`` subset).
    """

    category: MemoryCategory
    kept: MemoryEntry
    to_remove: tuple[MemoryEntry, ...]


@dataclass(frozen=True, slots=True)
class ConsolidationContext:
    """Per-run context threaded into every op call.

    Attributes:
        agent_id: Owning agent (log + cost-attribution context).
        trajectory_context: Distillation entries for LLM synthesis;
            empty for every non-LLM op and when distillation context
            is disabled.
    """

    agent_id: NotBlankStr
    trajectory_context: tuple[MemoryEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class OpResult:
    """What an op produced for one group.

    Attributes:
        summary_id: Backend id of the stored summary entry.
        removed_ids: Originals the op *successfully* deleted. For
            ``LLMSynthesisOp`` this excludes entries the prompt cap
            dropped (they survive for the next pass) and entries whose
            best-effort delete failed -- byte-identical with the
            pre-split monoliths.
        mode_assignments: Per-entry archival-mode assignments; non-empty
            only for the density-routing op so
            ``ConsolidationResult.mode_assignments`` stays
            byte-identical with the pre-split DualMode strategy.
    """

    summary_id: NotBlankStr
    removed_ids: tuple[NotBlankStr, ...]
    mode_assignments: tuple[ArchivalModeAssignment, ...] = field(default=())


@runtime_checkable
class EntrySelector(Protocol):
    """Decides which entries are consolidated and which is kept."""

    def select(
        self,
        entries: tuple[MemoryEntry, ...],
    ) -> tuple[SelectionGroup, ...]:
        """Group entries and pick the keep/remove split per group.

        Args:
            entries: The batch to consider.

        Returns:
            One :class:`SelectionGroup` per group eligible for
            consolidation (below-threshold groups are omitted).
        """
        ...


@runtime_checkable
class ConsolidationOp(Protocol):
    """Turns a group's to-remove set into a stored-summary payload."""

    async def prepare(
        self,
        agent_id: NotBlankStr,
    ) -> ConsolidationContext:
        """Build the per-run context once, before the group loop.

        Non-LLM ops return ``ConsolidationContext(agent_id=agent_id)``.
        ``LLMSynthesisOp`` overrides this to fetch distillation
        trajectory entries exactly once per run (byte-identical with
        the pre-split LLM monolith, which fetched before its group
        loop -- not per group).

        Returns:
            Result of type ``ConsolidationContext``.
        """
        ...

    async def consolidate(
        self,
        group: SelectionGroup,
        *,
        context: ConsolidationContext,
    ) -> OpResult:
        """Summarise + store + delete one group.

        The op owns its backend and applies its strategy's exact
        store/delete failure semantics internally. It operates on
        ``group.to_remove`` but may read ``group.kept`` (e.g. the
        density-routing op classifies over the *full* group --
        ``(kept,) + to_remove`` -- byte-identical with the pre-split
        DualMode strategy, whose majority vote counted the kept entry).

        Args:
            group: The selected group (category, kept, to_remove).
            context: Per-run context (agent id, trajectory entries).

        Returns:
            An :class:`OpResult` with the stored summary id and the
            ids actually removed (the composite only aggregates).
        """
        ...
