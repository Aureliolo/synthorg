"""Configuration for stakes assessment.

``StakesAssessmentConfig`` carries the heuristic rubric: a
complexity-to-base-stakes mapping plus keyword signal sets that elevate
stakes when a subtask's text mentions consequential or irreversible
work. The ``assessor`` field is the discriminator dispatched by
``build_stakes_assessor`` (see ``factory.py``).

The default complexity rules follow the design intent: simple work is
low-stakes, medium is normal, and complex/epic work is high-stakes.
Keyword sets bias the assessment upward (fail-safe) when the text names
architecture, irreversibility, production, security, or data-loss risk.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import Complexity, Stakes
from synthorg.core.types import NotBlankStr


class ComplexityStakesRule(BaseModel):
    """Maps a task complexity level to a base stakes level.

    Attributes:
        complexity: The complexity this rule matches.
        stakes: The base stakes assigned before keyword/priority bumps.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    complexity: Complexity = Field(description="Task complexity level")
    stakes: Stakes = Field(description="Base stakes level for this complexity")


DEFAULT_COMPLEXITY_STAKES_RULES: tuple[ComplexityStakesRule, ...] = (
    ComplexityStakesRule(complexity=Complexity.SIMPLE, stakes=Stakes.LOW),
    ComplexityStakesRule(complexity=Complexity.MEDIUM, stakes=Stakes.NORMAL),
    ComplexityStakesRule(complexity=Complexity.COMPLEX, stakes=Stakes.HIGH),
    ComplexityStakesRule(complexity=Complexity.EPIC, stakes=Stakes.HIGH),
)

# Import-time completeness guard (mirrors loop_selector.DEFAULT_AUTO_LOOP_RULES):
# every Complexity member must have a default base-stakes rule.
_covered = {r.complexity for r in DEFAULT_COMPLEXITY_STAKES_RULES}
if _covered != set(Complexity):
    _missing = set(Complexity) - _covered
    _msg = f"DEFAULT_COMPLEXITY_STAKES_RULES missing complexities: {_missing}"
    raise RuntimeError(_msg)
del _covered


# Words signalling high-stakes work: architecture / design decisions,
# security surfaces, deployment, and anything touching production data.
DEFAULT_HIGH_STAKES_KEYWORDS: tuple[NotBlankStr, ...] = (
    "architecture",
    "architectural",
    "design decision",
    "breaking change",
    "security",
    "authentication",
    "authorisation",
    "authorization",
    "credential",
    "secret",
    "encryption",
    "deploy",
    "deployment",
    "migration",
    "schema change",
    "payment",
    "billing",
    "rollback",
)

# Words signalling critical, irreversible work: a wrong answer here is
# costly to undo, so the assessment is pinned to the top tier.
DEFAULT_CRITICAL_STAKES_KEYWORDS: tuple[NotBlankStr, ...] = (
    "irreversible",
    "data loss",
    "drop table",  # lint-allow: persistence-boundary -- detection keyword
    "delete production",
    "production deployment",
    "destructive",
)


class StakesAssessmentConfig(BaseModel):
    """Rubric for the heuristic stakes assessor.

    Attributes:
        assessor: Discriminator selecting the assessor implementation.
        complexity_rules: Complexity-to-base-stakes mapping. Each
            complexity must appear at most once.
        high_stakes_keywords: Substrings that raise stakes to at least
            HIGH when present in a subtask's title or description.
        critical_stakes_keywords: Substrings that pin stakes to CRITICAL.
        elevate_on_critical_priority: When True, a CRITICAL-priority task
            is assessed at least HIGH stakes (priority is urgency, not
            stakes, but critical urgency is a conservative signal).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    assessor: NotBlankStr = Field(
        default="heuristic",
        description="Stakes assessor discriminator",
    )
    complexity_rules: tuple[ComplexityStakesRule, ...] = Field(
        default=DEFAULT_COMPLEXITY_STAKES_RULES,
        description="Complexity-to-base-stakes mapping rules",
    )
    high_stakes_keywords: tuple[NotBlankStr, ...] = Field(
        default=DEFAULT_HIGH_STAKES_KEYWORDS,
        description="Substrings that raise stakes to at least HIGH",
    )
    critical_stakes_keywords: tuple[NotBlankStr, ...] = Field(
        default=DEFAULT_CRITICAL_STAKES_KEYWORDS,
        description="Substrings that pin stakes to CRITICAL",
    )
    elevate_on_critical_priority: bool = Field(
        default=True,
        description="Assess CRITICAL-priority tasks at least HIGH stakes",
    )

    @model_validator(mode="after")
    def _validate_unique_complexities(self) -> Self:
        """Reject duplicate complexity entries in the rule list.

        Returns:
            ``self`` unchanged when every rule targets a distinct
            complexity.

        Raises:
            ValueError: When two rules reference the same
                :class:`Complexity` value.
        """
        seen: set[Complexity] = set()
        for rule in self.complexity_rules:
            if rule.complexity in seen:
                msg = f"Duplicate complexity in rules: {rule.complexity.value!r}"
                raise ValueError(msg)
            seen.add(rule.complexity)
        return self
