# module-kind: declarative
"""Durable active-principle domain model and the read seam for prompt build.

An :class:`ActivePrinciple` is a constitutional principle applied at runtime
by the self-improvement meta-loop (the prompt-tuning altitude), persisted in
the durable ``active_principles`` store. Unlike the YAML packs (static, shipped
in the repo) and ``config.custom`` (operator config), active principles are
written by ``PromptApplier.apply()`` and survive restart.

The store is read during prompt assembly. Because the prompt-build path
(:func:`synthorg.engine.strategy.adapter.inject_strategy_context`) is
synchronous, the read seam :class:`ActivePrincipleProvider` is SYNCHRONOUS: a
concrete provider loads a snapshot from the async repository at boot and
refreshes it when the applier writes, so ``load_and_merge`` can layer active
principles without an await. This mirrors the cached-config pattern used for
``self_improvement_config_of``.

Scope mapping (``ScopeKind`` + ``scope``) decides which agents see a principle:

* ``ALL`` -- every agent (``scope`` is the sentinel ``"all"``).
* ``ROLE`` -- agents whose ``role`` matches ``scope`` (case-insensitive).
* ``DEPARTMENT`` -- agents whose ``department`` matches ``scope``.
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.models import (
    ConstitutionalPrinciple,
    PrincipleSeverity,
)

#: Sentinel ``scope`` value for an organisation-wide active principle.
ALL_SCOPE: NotBlankStr = NotBlankStr("all")


class ScopeKind(StrEnum):
    """Which agents a durable active principle applies to."""

    ALL = "all"
    ROLE = "role"
    DEPARTMENT = "department"


class PrincipleEvolutionMode(StrEnum):
    """How an active principle layers onto the pack-derived principles.

    Mirrors :class:`synthorg.meta.EvolutionMode` value-for-value so the
    prompt applier can map a ``PromptChange.evolution_mode`` onto the durable
    record without the engine importing the meta layer (avoids an upward
    ``engine -> meta`` edge).
    """

    ORG_WIDE = "org_wide"
    OVERRIDE = "override"
    ADVISORY = "advisory"


class ActivePrinciple(BaseModel):
    """A durable constitutional principle applied by the meta-loop.

    Attributes:
        id: Stable primary key.
        principle_text: The rule text injected into system prompts.
        scope: ``"all"`` or a role / department name (per ``scope_kind``).
        scope_kind: Whether ``scope`` names the whole org, a role, or a dept.
        evolution_mode: How this principle layers onto pack principles.
        severity: Injection severity (defaults to ``WARNING``).
        created_at: First-written timestamp (UTC-aware).
        updated_at: Last-refreshed timestamp (UTC-aware).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    principle_text: NotBlankStr
    scope: NotBlankStr = ALL_SCOPE
    scope_kind: ScopeKind = ScopeKind.ALL
    evolution_mode: PrincipleEvolutionMode = PrincipleEvolutionMode.ORG_WIDE
    severity: PrincipleSeverity = PrincipleSeverity.WARNING
    created_at: AwareDatetime
    updated_at: AwareDatetime

    def to_constitutional(self) -> ConstitutionalPrinciple:
        """Project to the prompt-injection :class:`ConstitutionalPrinciple`.

        The durable id (a UUID) becomes the principle id so the merge can
        dedup active principles against pack / custom principles by id.

        Returns:
            The equivalent injectable principle.
        """
        return ConstitutionalPrinciple(
            id=NotBlankStr(f"active:{self.id}"),
            text=self.principle_text,
            category=NotBlankStr("active"),
            severity=self.severity,
        )


@runtime_checkable
class ActivePrincipleProvider(Protocol):
    """Synchronous read seam over the durable active-principle snapshot.

    Implemented by a cached provider that refreshes from the async
    repository at boot and on applier writes, so the synchronous prompt-build
    path can layer active principles without an await.
    """

    def list_active(
        self,
        *,
        role: str | None,
        department: str | None,
    ) -> tuple[ActivePrinciple, ...]:
        """Return active principles in scope for an agent.

        Includes every ``ALL``-scoped principle, plus ``ROLE``-scoped
        principles whose ``scope`` matches ``role`` and ``DEPARTMENT``-scoped
        principles whose ``scope`` matches ``department`` (case-insensitive).
        """
        ...


__all__ = [
    "ALL_SCOPE",
    "ActivePrinciple",
    "ActivePrincipleProvider",
    "PrincipleEvolutionMode",
    "ScopeKind",
]
