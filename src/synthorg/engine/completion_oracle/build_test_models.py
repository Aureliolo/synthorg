# module-kind: declarative
"""Value objects for the Layer 1 execution-grounded build/test gate.

A ``BuildTestOracle`` evaluation is a pure function of a task's
grounding classification and its persisted ``CodeExecutionRecord`` rows.
The result is an :class:`OracleEvaluation` that the completion-gate chain,
the run-outcome re-source, and the execution-aware grader all consume, so
one core computes the build/test verdict and every consumer agrees.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


class GroundingRequirement(StrEnum):
    """Whether a task's completion requires build/test grounding.

    Members:
        REQUIRED: The task produces (or declares) code / tests, so the
            oracle must see a passing test run before it can be "done".
        NOT_APPLICABLE: The task produces docs / plans / decisions, so
            build/test grounding does not apply and the oracle abstains.
    """

    REQUIRED = "required"
    NOT_APPLICABLE = "not_applicable"


class OracleVerdict(StrEnum):
    """The build/test oracle's verdict for a completing task.

    Members:
        VERIFIED: A REQUIRED task whose latest test run passed.
        BUILD_TEST_FAILED: The latest test run failed (blocks regardless
            of classification; a red test is ground truth).
        UNVERIFIED: A REQUIRED task with no passing test evidence (no
            record, or the record store could not be read). The stub the
            oracle exists to catch; blocks.
        NOT_APPLICABLE: A non-code task with no failing test evidence;
            the oracle abstains and completion proceeds.
        CHECKER_UNAVAILABLE: The execution-record store is unwired (a
            persistence-less boot). Structural absence of the checker,
            not evidence of failure; passes through.
    """

    VERIFIED = "verified"
    BUILD_TEST_FAILED = "build_test_failed"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"
    CHECKER_UNAVAILABLE = "checker_unavailable"


class OracleEvaluation(BaseModel):
    """The build/test oracle's structured result for one completing task.

    Attributes:
        verdict: The build/test verdict.
        requirement: Whether the task required build/test grounding.
        reason: Human-readable rationale for the verdict.
        tests_seen: Number of ``purpose='tests'`` execution records the
            oracle inspected (bounded by the query page).
        tests_failed: How many of those records did not pass.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verdict: OracleVerdict
    requirement: GroundingRequirement
    reason: NotBlankStr
    tests_seen: int = Field(default=0, ge=0)
    tests_failed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _counts_consistent(self) -> Self:
        """``tests_failed`` cannot exceed ``tests_seen``.

        Returns:
            The validated evaluation.

        Raises:
            ValueError: If more tests failed than were inspected.
        """
        if self.tests_failed > self.tests_seen:
            msg = (
                f"tests_failed ({self.tests_failed}) cannot exceed "
                f"tests_seen ({self.tests_seen})"
            )
            raise ValueError(msg)
        return self

    @property
    def blocks_completion(self) -> bool:
        """True when the verdict must route a completing task back to rework.

        Only a failing test run or an unverified REQUIRED code task block.
        ``CHECKER_UNAVAILABLE`` (structural checker absence) and
        ``NOT_APPLICABLE`` / ``VERIFIED`` do not.
        """
        return self.verdict in (
            OracleVerdict.BUILD_TEST_FAILED,
            OracleVerdict.UNVERIFIED,
        )
