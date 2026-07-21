# module-kind: code
"""Retrospective material assembly, idempotency check, and memory writes.

Standalone helpers for the SHIP-time retrospective, kept out of the service so
the orchestration stays small. The write side is deliberately per-item
best-effort: one learning that a capability gate rejects (an org write by a
non-capable lead) or that the backend refuses must not lose the rest.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.plan import Plan
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.retro_models import (
    RetrospectiveDraft,
    org_category_for,
    retro_object_tag,
)
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.memory.org.models import (
    OrgFactAuthor,
    OrgFactWriteRequest,
    OrgMemoryQuery,
)
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.retrospective import (
    RETRO_AGENT_LEARNING_WRITTEN,
    RETRO_ORG_LEARNING_WRITTEN,
    RETRO_WRITE_FAILED,
)

logger = get_logger(__name__)

#: Tag stamped on every retrospective-sourced entry, so a recall or an audit
#: can tell a retro learning from other org knowledge.
_RETRO_TAG: Final[NotBlankStr] = NotBlankStr("retro")

#: How many recent org facts the idempotency backstop scans. The rollup's
#: edge-once detection is the primary guard; this only catches a restart
#: mid-capture, so a bounded recent window is enough.
_IDEMPOTENCY_SCAN_LIMIT: Final[int] = 100

#: Confidence recorded on a per-agent retro learning: the lead vouched for it
#: in the session, so it is authoritative, not a self-reported guess.
_LEARNING_CONFIDENCE: Final[float] = 1.0


class WriteResult(BaseModel):
    """Counts of learnings that actually landed in memory.

    Attributes:
        org_written: Org learnings persisted to organisational memory.
        agent_written: Per-agent learnings persisted to agent memory.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    org_written: int = Field(default=0, ge=0, description="Org learnings written")
    agent_written: int = Field(default=0, ge=0, description="Agent learnings written")


def build_retro_material(plan: Plan, project: Project) -> str:
    """Assemble the finished-work material the lead distils from.

    Returns:
        A human-readable summary of the objective, its acceptance criteria,
        the completed plan items, and the team.
    """
    lines: list[str] = [
        f"Objective: {plan.objective_title}",
        f"Initiative: {project.name}",
    ]
    if plan.objective_criteria:
        lines.append("Objective acceptance criteria:")
        lines.extend(f"  - {c}" for c in plan.objective_criteria)
    lines.append(f"Team size: {len(project.team)}")
    lines.append("Completed plan items:")
    for item in plan.items:
        lines.append(f"  - [{item.kind.value}] {item.title}")
        lines.extend(f"      done when: {c}" for c in item.acceptance_criteria)
    return "\n".join(lines)


async def already_captured(
    org_backend: OrgMemoryBackend,
    *,
    project_id: str,
) -> bool:
    """Return whether a retrospective already exists for *project_id*.

    A restart-safety backstop to the rollup's edge-once detection: it scans a
    bounded window of recent procedure/convention facts for the objective's
    retrospective tag. A miss (older than the window) simply lets capture run
    again; the write gate then dedups the learnings, so the cost is a repeat
    session, never a corrupt double-write.

    Returns:
        ``True`` when a recent org fact carries this objective's retro tag.
    """
    tag = retro_object_tag(project_id)
    facts = await org_backend.query(
        OrgMemoryQuery(
            categories=frozenset(
                {OrgFactCategory.PROCEDURE, OrgFactCategory.CONVENTION}
            ),
            limit=_IDEMPOTENCY_SCAN_LIMIT,
        )
    )
    return any(tag in fact.tags for fact in facts)


async def write_learnings(  # noqa: PLR0913 -- explicit-deps write helper
    draft: RetrospectiveDraft,
    *,
    lead: AgentIdentity,
    project: Project,
    memory_backend: MemoryBackend,
    org_backend: OrgMemoryBackend,
    clock: Clock,
) -> WriteResult:
    """Persist the draft's learnings to org and agent memory.

    Org learnings are authored by the lead, so a non-capable lead's writes are
    refused at the org access gate (by design); each refusal is logged and the
    rest continue. Per-agent learnings are only written for agents actually on
    the initiative, so a hallucinated agent id lands nowhere.

    Returns:
        The counts that actually persisted.
    """
    del clock  # backends stamp their own timestamps
    tags = (retro_object_tag(str(project.id)), _RETRO_TAG)
    org_written = await _write_org_learnings(draft, lead, tags, org_backend)
    agent_written = await _write_agent_learnings(
        draft, project, lead, tags, memory_backend
    )
    return WriteResult(org_written=org_written, agent_written=agent_written)


async def _write_org_learnings(
    draft: RetrospectiveDraft,
    lead: AgentIdentity,
    tags: tuple[NotBlankStr, ...],
    org_backend: OrgMemoryBackend,
) -> int:
    """Write the org-scoped learnings, counting those that land.

    Returns:
        The number of org learnings persisted.
    """
    author = OrgFactAuthor(
        agent_id=NotBlankStr(str(lead.id)),
        role=NotBlankStr(lead.role),
        autonomy_level=lead.autonomy_level,
    )
    written = 0
    for learning in draft.org_learnings:
        try:
            await org_backend.write(
                OrgFactWriteRequest(
                    content=learning.content,
                    category=org_category_for(learning.kind),
                    tags=tags,
                ),
                author=author,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- one refused write must not lose the rest
            reraise_critical(exc)
            logger.warning(
                RETRO_WRITE_FAILED,
                scope="org",
                lead_id=str(lead.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            continue
        written += 1
        logger.info(RETRO_ORG_LEARNING_WRITTEN, kind=learning.kind)
    return written


async def _write_agent_learnings(
    draft: RetrospectiveDraft,
    project: Project,
    lead: AgentIdentity,
    tags: tuple[NotBlankStr, ...],
    memory_backend: MemoryBackend,
) -> int:
    """Write the per-agent learnings for genuine contributors only.

    Returns:
        The number of per-agent learnings persisted.
    """
    members = set(project.team) | {NotBlankStr(str(lead.id))}
    written = 0
    for learning in draft.agent_learnings:
        if learning.agent_id not in members:
            logger.warning(
                RETRO_WRITE_FAILED,
                scope="agent",
                reason="agent_not_on_initiative",
                agent_id=learning.agent_id,
            )
            continue
        try:
            await memory_backend.store(
                learning.agent_id,
                MemoryStoreRequest(
                    category=MemoryCategory.EPISODIC,
                    content=learning.content,
                    metadata=MemoryMetadata(
                        source=NotBlankStr("retrospective"),
                        confidence=_LEARNING_CONFIDENCE,
                        tags=tags,
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- one refused write must not lose the rest
            reraise_critical(exc)
            logger.warning(
                RETRO_WRITE_FAILED,
                scope="agent",
                agent_id=learning.agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            continue
        written += 1
        logger.info(RETRO_AGENT_LEARNING_WRITTEN, agent_id=learning.agent_id)
    return written
