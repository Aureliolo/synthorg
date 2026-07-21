"""The golden recall set and its scorer.

A recall-only eval rewards a system that returns everything, and
over-retrieval is precisely what degrades a downstream answer: injected
distractors compound accuracy loss, and lower query-to-evidence
similarity accelerates it. So every case names both what MUST be
recalled and what MUST NOT, and the scorer reports precision alongside
recall plus an explicit pollution rate.

Abstention cases carry no expected memories at all. Recalling nothing is
the correct answer there, not a failure to retrieve.
"""

from dataclasses import dataclass, field
from typing import Final

from synthorg.core.memory_enums import MemoryCategory


@dataclass(frozen=True, slots=True)
class GoldenMemory:
    """One memory seeded into the corpus under evaluation.

    Attributes:
        memory_id: Stable identifier the cases refer to.
        content: The stored text.
        category: Memory category.
        tags: Tags carried by the entry, used by topic scoping.
        agent_id: Owning agent; varied to exercise layer isolation.
    """

    memory_id: str
    content: str
    category: MemoryCategory = MemoryCategory.SEMANTIC
    tags: tuple[str, ...] = ()
    agent_id: str = "agent-1"


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One recall scenario scored against the corpus.

    Attributes:
        name: Human-readable case identifier.
        query: The task title driving recall.
        expected: Memories that must be recalled.
        forbidden: Memories that must never be recalled.
        agent_id: The agent performing the recall.
        why: What the case is protecting against.
    """

    name: str
    query: str
    expected: frozenset[str] = field(default_factory=frozenset)
    forbidden: frozenset[str] = field(default_factory=frozenset)
    agent_id: str = "agent-1"
    why: str = ""

    @property
    def is_abstention(self) -> bool:
        """Whether recalling nothing is the correct outcome."""
        return not self.expected


CORPUS: Final[tuple[GoldenMemory, ...]] = (
    GoldenMemory(
        memory_id="rollback-lesson",
        content=(
            "Rollback of a deployment must drain the database connection "
            "pool first or the migration deadlocks."
        ),
        category=MemoryCategory.PROCEDURAL,
        tags=("rollback", "deployment"),
    ),
    GoldenMemory(
        memory_id="scaling-lesson",
        content=(
            "Kubernetes scaling events during an incident should be "
            "paused until the postmortem identifies the trigger."
        ),
        category=MemoryCategory.PROCEDURAL,
        tags=("scaling", "kubernetes"),
    ),
    GoldenMemory(
        memory_id="migration-fact",
        content=(
            "The database migration runner holds an exclusive lock for "
            "the duration of the migration."
        ),
        category=MemoryCategory.SEMANTIC,
        tags=("database", "migration"),
    ),
    GoldenMemory(
        memory_id="incident-episode",
        content=(
            "During the last incident the postmortem found a rollback "
            "had been attempted twice concurrently."
        ),
        category=MemoryCategory.EPISODIC,
        tags=("incident", "postmortem"),
    ),
    GoldenMemory(
        memory_id="other-agent-secret",
        content=(
            "Kubernetes scaling limits for the payments cluster were "
            "raised after the incident."
        ),
        category=MemoryCategory.SEMANTIC,
        tags=("scaling", "kubernetes"),
        agent_id="agent-2",
    ),
)

CASES: Final[tuple[GoldenCase, ...]] = (
    GoldenCase(
        name="direct-topic-match",
        query="rollback deployment",
        expected=frozenset({"rollback-lesson"}),
        forbidden=frozenset({"scaling-lesson"}),
        why="The lesson for this exact task must surface.",
    ),
    GoldenCase(
        name="related-vocabulary",
        query="database migration lock",
        expected=frozenset({"migration-fact"}),
        forbidden=frozenset({"scaling-lesson"}),
        why="Recall keys on meaning, not on a literal title match.",
    ),
    GoldenCase(
        name="procedural-scope-holds",
        query="kubernetes scaling",
        expected=frozenset({"scaling-lesson"}),
        forbidden=frozenset({"rollback-lesson"}),
        why=(
            "A procedural lesson tagged for another topic must not ride "
            "along on shared incident vocabulary."
        ),
    ),
    GoldenCase(
        name="synonym-recall",
        query="revert the release",
        expected=frozenset({"rollback-lesson"}),
        forbidden=frozenset({"scaling-lesson", "migration-fact"}),
        why=(
            "The lesson shares no term with the task, only meaning. This "
            "is what dense retrieval is bought for and what term "
            "matching cannot do at any threshold."
        ),
    ),
    GoldenCase(
        name="synonym-recall-capacity",
        query="autoscale capacity during an outage",
        expected=frozenset({"scaling-lesson"}),
        forbidden=frozenset({"rollback-lesson", "migration-fact"}),
        why="A second synonym case, so one lucky match cannot carry the score.",
    ),
    GoldenCase(
        name="abstain-on-unrelated-work",
        query="quarterly marketing budget",
        forbidden=frozenset({"rollback-lesson", "scaling-lesson", "migration-fact"}),
        why=(
            "Nothing in the corpus bears on this task. Injecting anything "
            "is pollution, and abstention is the correct answer."
        ),
    ),
    GoldenCase(
        name="layer-isolation",
        query="kubernetes scaling",
        agent_id="agent-1",
        forbidden=frozenset({"other-agent-secret"}),
        expected=frozenset({"scaling-lesson"}),
        why="One agent's memory must never surface for another.",
    ),
)


@dataclass(frozen=True, slots=True)
class RecallScore:
    """Aggregate quality of one configuration over the golden set.

    Attributes:
        precision: Share of recalled memories that were expected.
        recall: Share of expected memories that were recalled.
        pollution: Share of cases that surfaced a forbidden memory.
        abstention_accuracy: Share of abstention cases answered empty.
    """

    precision: float
    recall: float
    pollution: float
    abstention_accuracy: float

    @property
    def f1(self) -> float:
        """Harmonic mean of precision and recall."""
        if self.precision + self.recall == 0.0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)


def score_recall(
    results: dict[str, frozenset[str]],
    cases: tuple[GoldenCase, ...] = CASES,
) -> RecallScore:
    """Score recalled ids per case against the golden expectations.

    Precision and recall are micro-averaged over cases that expect
    something; abstention cases contribute to pollution and abstention
    accuracy only, since precision is undefined without a positive.

    Args:
        results: Case name to the set of memory ids recalled.
        cases: The cases to score.

    Returns:
        The aggregate score.
    """
    true_positives = 0
    retrieved = 0
    expected_total = 0
    polluted = 0
    abstentions = 0
    correct_abstentions = 0

    for case in cases:
        recalled = results.get(case.name, frozenset())
        if case.forbidden & recalled:
            polluted += 1
        if case.is_abstention:
            abstentions += 1
            if not recalled:
                correct_abstentions += 1
            continue
        true_positives += len(case.expected & recalled)
        retrieved += len(recalled)
        expected_total += len(case.expected)

    return RecallScore(
        precision=true_positives / retrieved if retrieved else 0.0,
        recall=true_positives / expected_total if expected_total else 0.0,
        pollution=polluted / len(cases) if cases else 0.0,
        abstention_accuracy=(correct_abstentions / abstentions if abstentions else 1.0),
    )
