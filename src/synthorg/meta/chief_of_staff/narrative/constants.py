# module-kind: code
"""Named constants for the run-narrative engine.

Bounds keep a long run from producing an unbounded prompt or an
unbounded document; section titles and tag/slug prefixes are centralised
so the assembler, reader, and service agree on one spelling.
"""

from typing import Final

from synthorg.core.types import NotBlankStr

# ── Source-gathering bounds ──────────────────────────────────────────

# A run's frames are paged out of the append-only store; this caps how
# many the reader pulls so a multi-thousand-turn run cannot blow memory
# or the downstream prompt. The newest frames are the most relevant to a
# post-run account, and the repository returns newest-first.
MAX_FRAMES_SCANNED: Final[int] = 2000

# Page size for draining the append-only frame store; the reader pages
# newest-first until MAX_FRAMES_SCANNED or the run is exhausted.
FRAME_PAGE_SIZE: Final[int] = 200

# Upper bound on the brain entries pulled for one run before partitioning
# into decisions and open items.
BRAIN_LIST_LIMIT: Final[int] = 500

# Decisions are the spine of the narrative; 50 bounds the decision-log
# section (and the per-decision brain lookups) without truncating any
# realistic brief.
MAX_DECISIONS: Final[int] = 50

# The "who did what" roster lists at most this many agents, ordered by
# contribution volume; a run with more contributors omits the
# lower-volume agents from the roster.
MAX_AGENTS_LISTED: Final[int] = 25

# Per-agent tool roster shown in the contribution bullet.
MAX_TOOLS_PER_AGENT: Final[int] = 8

# Open items (questions / blockers / risks still live) shown in the
# narrative's standing-items section.
MAX_OPEN_ITEMS: Final[int] = 30

# Sources section size cap (provenance links).
MAX_SOURCES: Final[int] = 60

# ── Identity ─────────────────────────────────────────────────────────

# Author stamped on the narrative living doc (the Chief-of-Staff acting
# as narrator, not a project agent).
NARRATOR_AGENT_ID: Final[NotBlankStr] = NotBlankStr("chief-of-staff:narrator")

# Tag prefix keying a narrative to its brief (root task). The narrator
# keys idempotent update-in-place on this, so re-completing the same
# brief refreshes one doc rather than spawning a duplicate per run.
TASK_TAG_PREFIX: Final[str] = "task:"

# Tag prefix recording the execution that produced the latest narrative.
# Carried for provenance, not for deduplication (a fresh execution id is
# minted per run).
EXECUTION_TAG_PREFIX: Final[str] = "execution:"

# A DecisionBlock bounds its decision + rationale text at 4096 chars,
# while a brain rationale may run to 8192; the reducer clips to this so a
# rich-rationale decision cannot fail block construction and silently
# drop the whole narrative.
DECISION_TEXT_MAX: Final[int] = 4096

# Tag marking a doc as a run narrative (alongside the doc_type).
NARRATIVE_TAG: Final[NotBlankStr] = NotBlankStr("run-narrative")

# ── Section titles ───────────────────────────────────────────────────

SECTION_SUMMARY: Final[str] = "Executive summary"
SECTION_DECISIONS: Final[str] = "Decisions"
SECTION_CONTRIBUTIONS: Final[str] = "Who did what"
SECTION_OUTCOMES: Final[str] = "Outcomes"
SECTION_OPEN_ITEMS: Final[str] = "Open items"
SECTION_SOURCES: Final[str] = "Sources"

# ── Fallback prose ───────────────────────────────────────────────────

# Used when the provider call fails or returns empty: the structured
# blocks still carry the trustworthy facts, so the doc degrades to a
# fact-only narrative rather than failing.
FALLBACK_SUMMARY: Final[str] = (
    "Automated summary unavailable for this run; the decisions, "
    "contributions, and outcomes below are drawn directly from the "
    "project record."
)
