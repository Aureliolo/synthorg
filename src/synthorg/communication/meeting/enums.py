"""Meeting protocol enumerations (see Communication design page)."""

from enum import StrEnum


class MeetingProtocolType(StrEnum):
    """Strategy for conducting a meeting.

    Members:
        ROUND_ROBIN: Sequential turns, full transcript context.
        POSITION_PAPERS: Parallel independent papers, then synthesis.
        STRUCTURED_PHASES: Phased agenda with conditional discussion.
    """

    ROUND_ROBIN = "round_robin"
    POSITION_PAPERS = "position_papers"
    STRUCTURED_PHASES = "structured_phases"


class MeetingPhase(StrEnum):
    """Phase within a meeting protocol execution.

    Phases are scoped to specific protocols:

    - Round-robin: ``ROUND_ROBIN_TURN``, ``SUMMARY``
    - Position papers: ``POSITION_PAPER``, ``SYNTHESIS``
    - Structured phases: ``AGENDA_BROADCAST``, ``INPUT_GATHERING``,
      ``DISCUSSION``, ``SYNTHESIS``

    Attributes:
        AGENDA_BROADCAST: Initial agenda distribution.
        ROUND_ROBIN_TURN: Single turn in round-robin protocol.
        POSITION_PAPER: Independent position paper submission.
        INPUT_GATHERING: Parallel input collection in structured phases.
        DISCUSSION: Conflict-driven discussion round.
        SYNTHESIS: Leader synthesizes all inputs into decisions.
        SUMMARY: Final summary generation.
        PREMORTEM: Risk identification after synthesis.
        DEVIL_ADVOCATE: Dissent injection on premature consensus.
    """

    AGENDA_BROADCAST = "agenda_broadcast"
    ROUND_ROBIN_TURN = "round_robin_turn"
    POSITION_PAPER = "position_paper"
    INPUT_GATHERING = "input_gathering"
    DISCUSSION = "discussion"
    SYNTHESIS = "synthesis"
    SUMMARY = "summary"
    PREMORTEM = "premortem"
    DEVIL_ADVOCATE = "devil_advocate"


class ConflictDetectorType(StrEnum):
    """Selected conflict-detection strategy for the structured-phases protocol.

    Each value maps to a concrete detector class registered in
    :mod:`synthorg.communication.meeting.factory`.

    Attributes:
        KEYWORD: Keyword-marker detection (default; fast, deterministic).
        STRUCTURED: Structured JSON field comparison; zero-cost,
            deterministic, requires position JSON in responses.
        LLM_JUDGE: Parse a structured judgment from the leader's
            response (JSON ``conflicts`` field or ``JUDGE:`` markers).
        EMBEDDING: Embedding-similarity-based detection (placeholder
            implementation; raises NotImplementedError until embedding
            infrastructure is available).
        HYBRID: Combine embedding similarity with a keyword fallback.
        AUTO: Pick STRUCTURED when JSON is present, fall back to
            KEYWORD otherwise.
    """

    KEYWORD = "keyword"
    STRUCTURED = "structured"
    LLM_JUDGE = "llm_judge"
    EMBEDDING = "embedding"
    HYBRID = "hybrid"
    AUTO = "auto"


class MeetingStatus(StrEnum):
    """Lifecycle status of a meeting.

    Currently produced by ``MeetingOrchestrator``:
    ``COMPLETED``, ``FAILED``, ``BUDGET_EXHAUSTED``.

    Reserved for future lifecycle management:
    ``SCHEDULED``, ``IN_PROGRESS``, ``CANCELLED``.

    Attributes:
        SCHEDULED: Meeting is planned but not yet started.
        IN_PROGRESS: Meeting is currently running.
        COMPLETED: Meeting finished successfully.
        FAILED: Meeting terminated due to an error.
        CANCELLED: Meeting was cancelled before completion.
        BUDGET_EXHAUSTED: Meeting stopped because token budget ran out.
    """

    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
