# module-kind: code
"""Case and grade models for the pin-validation benchmark.

A pin-validation run streams one :class:`PinTestCase` per registered
prompt purpose and grades each one into a :class:`PinGrade`. Both are
frozen so a case handed to the probe runner cannot be mutated between
the probe and the grade that reads the same pin back out of it.
"""

import copy
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

#: Upper bound on the probe prompt and the expected fingerprint, so a
#: malformed metadata payload cannot balloon a case.
_MAX_FIELD_CHARS: Final[int] = 65536

#: Upper bound on a grade explanation.
_MAX_EXPLANATION_CHARS: Final[int] = 2048


class PinTestCase(BaseModel):
    """One prompt class's pin-validation case.

    Attributes:
        id: The prompt class id under test.
        input_data: The canonical probe prompt.
        expected_output: The committed golden fingerprint, empty when the
            class is absent from the golden.
        metadata: The serialised pin payload the runner and grader read.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Prompt class id under test")
    input_data: str = Field(
        max_length=_MAX_FIELD_CHARS,
        description="Canonical probe prompt",
    )
    expected_output: str = Field(
        max_length=_MAX_FIELD_CHARS,
        description="Committed golden fingerprint",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Serialised pin payload",
    )

    @model_validator(mode="before")
    @classmethod
    def _deep_copy_metadata(cls, data: object) -> object:
        """Deep-copy the supplied metadata dict at the construction boundary.

        Returns:
            The input with ``metadata`` replaced by a deep copy, so a
            later mutation of the caller's dict cannot reach into this
            frozen record's nested metadata.
        """
        if isinstance(data, dict) and isinstance(data.get("metadata"), dict):
            return {**data, "metadata": copy.deepcopy(data["metadata"])}
        return data


class PinGrade(BaseModel):
    """Drift verdict for one prompt class.

    Attributes:
        passed: Whether the live fingerprint matched the golden.
        score: Numeric score (0.0-1.0).
        explanation: Human-readable grading rationale.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    passed: bool = Field(description="Whether the live fingerprint matched")
    score: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Numeric score (0.0-1.0)",
    )
    explanation: str = Field(
        max_length=_MAX_EXPLANATION_CHARS,
        description="Human-readable grading rationale",
    )


__all__ = ["PinGrade", "PinTestCase"]
