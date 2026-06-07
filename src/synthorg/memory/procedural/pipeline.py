"""End-to-end pipeline for procedural memory auto-generation.

Extracts a failure analysis payload from execution and recovery data,
invokes the proposer, stores the resulting procedural memory entry,
and optionally materializes a SKILL.md file.
"""

import re
from pathlib import Path

import yaml

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.engine.recovery import RecoveryResult
from synthorg.engine.sanitization import sanitize_message
from synthorg.memory.filter import NON_INFERABLE_TAG
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.memory.procedural.models import (
    FailureAnalysisPayload,
    ProceduralMemoryConfig,
    ProceduralMemoryProposal,
)
from synthorg.memory.procedural.proposer import ProceduralMemoryProposer
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.procedural_memory import (
    PROCEDURAL_MEMORY_ERROR,
    PROCEDURAL_MEMORY_PAYLOAD_BUILT,
    PROCEDURAL_MEMORY_SKILL_MD,
    PROCEDURAL_MEMORY_SKIPPED,
    PROCEDURAL_MEMORY_START,
    PROCEDURAL_MEMORY_STORE_FAILED,
    PROCEDURAL_MEMORY_STORED,
)

logger = get_logger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _build_payload(
    execution_result: ExecutionResult,
    recovery_result: RecoveryResult,
) -> FailureAnalysisPayload:
    """Build a failure analysis payload from execution and recovery data.

    Flattens tool call names from all turn records in the execution
    result and extracts task metadata from the recovery result's
    task execution and context snapshot.

    The ``error_message`` is sanitized via ``sanitize_message`` to
    strip file paths, URLs, and non-printable characters (truncated
    to 200 characters) before it reaches the proposer LLM.

    Args:
        execution_result: Completed execution result with turn records.
        recovery_result: Recovery result with task and error context.

    Returns:
        Structured payload for the proposer LLM.
    """
    task = recovery_result.task_execution.task
    tool_calls: list[str] = []
    for turn in execution_result.turns:
        tool_calls.extend(turn.tool_calls_made)

    return FailureAnalysisPayload(
        task_id=str(task.id),
        task_title=task.title,
        task_description=task.description,
        task_type=task.type,
        error_message=sanitize_message(recovery_result.error_message),
        strategy_type=recovery_result.strategy_type,
        termination_reason=execution_result.termination_reason.value,
        turn_count=recovery_result.context_snapshot.turn_count,
        tool_calls_made=tuple(tool_calls),
        retry_count=recovery_result.task_execution.retry_count,
        max_retries=task.max_retries,
        can_reassign=recovery_result.can_reassign,
    )


def _format_procedural_content(
    proposal: ProceduralMemoryProposal,
) -> str:
    """Format a proposal into three-tier progressive disclosure text.

    * Discovery: retrieval signal (~100 tokens).
    * Activation: condition/action/rationale.
    * Execution: ordered steps for applying the knowledge.

    Args:
        proposal: Validated proposer output.

    Returns:
        Formatted memory content string.
    """
    parts = [
        f"[DISCOVERY] {proposal.discovery}",
        f"[CONDITION] {proposal.condition}",
        f"[ACTION] {proposal.action}",
        f"[RATIONALE] {proposal.rationale}",
    ]
    if proposal.execution_steps:
        steps = "\n".join(
            f"  {i}. {step}" for i, step in enumerate(proposal.execution_steps, 1)
        )
        parts.append(f"[EXECUTION]\n{steps}")
    return "\n\n".join(parts)


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug.

    Returns:
        Result of type ``str``.
    """
    return _SLUG_RE.sub("-", text.lower()).strip("-")[:80]


def materialize_skill_md(
    proposal: ProceduralMemoryProposal,
    task_id: str,
    directory: str,
) -> Path:
    """Write a proposal as a SKILL.md file for git-native versioning.

    The file follows the Agent Skills (agentskills.io) format with
    YAML frontmatter and three-tier progressive disclosure sections.

    Args:
        proposal: Validated proposer output.
        task_id: Task identifier (used in filename).
        directory: Target directory for the SKILL.md file.

    Returns:
        Path to the written file.
    """
    slug = _slugify(proposal.discovery[:60]) or "skill"
    safe_task_id = _slugify(task_id) or "task"
    filename = f"SKILL-{safe_task_id}-{slug}.md"
    path = Path(directory) / filename

    frontmatter: dict[str, object] = {
        "name": slug,
        "description": proposal.discovery,
        "trigger": proposal.condition,
        "confidence": proposal.confidence,
        "tags": list(proposal.tags),
        "source": f"failure:{task_id}",
    }
    fm_text = yaml.safe_dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).rstrip("\n")

    steps_block = ""
    if proposal.execution_steps:
        lines = "\n".join(
            f"{i}. {step}" for i, step in enumerate(proposal.execution_steps, 1)
        )
        steps_block = f"\n## Execution Steps\n\n{lines}\n"

    content = (
        f"---\n{fm_text}\n---\n\n"
        f"# {proposal.discovery}\n\n"
        f"## Condition\n\n{proposal.condition}\n\n"
        f"## Action\n\n{proposal.action}\n\n"
        f"## Rationale\n\n{proposal.rationale}\n"
        f"{steps_block}"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


async def _try_build_payload(
    execution_result: ExecutionResult,
    recovery_result: RecoveryResult,
    agent_id: str,
    task_id: str,
) -> FailureAnalysisPayload | None:
    """Build payload with error handling (never fatal).

    Returns:
        The resulting ``FailureAnalysisPayload``, or ``None`` when unavailable.
    """
    try:
        payload = _build_payload(execution_result, recovery_result)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            PROCEDURAL_MEMORY_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            context="payload construction failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    logger.debug(
        PROCEDURAL_MEMORY_PAYLOAD_BUILT,
        agent_id=agent_id,
        task_id=task_id,
        turn_count=payload.turn_count,
        tool_count=len(payload.tool_calls_made),
    )
    return payload


async def _store_and_materialize(
    proposal: ProceduralMemoryProposal,
    agent_id: str,
    task_id: str,
    memory_backend: MemoryBackend,
    config: ProceduralMemoryConfig | None,
) -> NotBlankStr | None:
    """Store procedural memory and optionally materialize SKILL.md.

    Returns:
        The resulting ``NotBlankStr``, or ``None`` when unavailable.
    """
    content = _format_procedural_content(proposal)
    tags = (NON_INFERABLE_TAG, *proposal.tags)
    request = MemoryStoreRequest(
        category=MemoryCategory.PROCEDURAL,
        content=content,
        metadata=MemoryMetadata(
            source=f"failure:{task_id}",
            confidence=proposal.confidence,
            tags=tags,
        ),
    )
    try:
        memory_id = await memory_backend.store(agent_id, request)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            PROCEDURAL_MEMORY_STORE_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    logger.info(
        PROCEDURAL_MEMORY_STORED,
        agent_id=agent_id,
        task_id=task_id,
        memory_id=memory_id,
        confidence=proposal.confidence,
    )

    if config is not None and config.skill_md_directory is not None:
        try:
            skill_path = materialize_skill_md(
                proposal,
                task_id,
                config.skill_md_directory,
            )
            logger.info(
                PROCEDURAL_MEMORY_SKILL_MD,
                agent_id=agent_id,
                task_id=task_id,
                path=str(skill_path),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PROCEDURAL_MEMORY_SKILL_MD,
                agent_id=agent_id,
                task_id=task_id,
                context="SKILL.md write failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    return memory_id


async def propose_procedural_memory(  # noqa: PLR0913
    execution_result: ExecutionResult,
    recovery_result: RecoveryResult,
    agent_id: NotBlankStr,
    task_id: NotBlankStr,
    *,
    proposer: ProceduralMemoryProposer,
    memory_backend: MemoryBackend,
    config: ProceduralMemoryConfig | None = None,
) -> NotBlankStr | None:
    """Build payload, propose, and store a procedural memory entry.

    Returns the backend-assigned memory ID if stored, ``None`` if
    the proposal was skipped or storage failed.  Never raises
    (except ``MemoryError`` / ``RecursionError``).

    Args:
        execution_result: Completed execution result.
        recovery_result: Recovery result from the failed execution.
        agent_id: Agent that failed the task.
        task_id: Failed task identifier.
        proposer: LLM-based proposer instance.
        memory_backend: Backend for storing the procedural memory.
        config: Optional config.  Only ``skill_md_directory`` is used
            here; other fields are consumed by the proposer at
            construction time.

    Returns:
        Memory ID or ``None``.
    """
    logger.info(
        PROCEDURAL_MEMORY_START,
        agent_id=agent_id,
        task_id=task_id,
    )

    payload = await _try_build_payload(
        execution_result,
        recovery_result,
        agent_id,
        task_id,
    )
    if payload is None:
        return None

    try:
        proposal = await proposer.propose(payload)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            PROCEDURAL_MEMORY_SKIPPED,
            agent_id=agent_id,
            task_id=task_id,
            reason="proposer_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    if proposal is None:
        logger.info(
            PROCEDURAL_MEMORY_SKIPPED,
            agent_id=agent_id,
            task_id=task_id,
            reason="proposer_returned_none",
        )
        return None

    return await _store_and_materialize(
        proposal,
        agent_id,
        task_id,
        memory_backend,
        config,
    )
