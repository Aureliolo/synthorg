"""Structured handoff artifact for inter-stage data transfer.

A ``HandoffArtifact`` captures the payload, metadata, and probe
context passed between workflow stages -- particularly from a
generator to a verification evaluator.
"""

import copy
import json
from collections.abc import Mapping
from typing import Self

from pydantic import Field, field_validator, model_validator

from synthorg.core.structured_artifact import StructuredArtifact
from synthorg.core.types import NotBlankStr
from synthorg.engine.quality.verification import (
    AtomicProbe,
    VerificationRubric,
)
from synthorg.observability import safe_error_description


class HandoffArtifact(StructuredArtifact):
    """Structured data transferred between workflow stages.

    Carries the payload from one agent to the next, along with
    acceptance probes and an optional rubric for the evaluator.

    The ``from_agent_id`` must differ from ``to_agent_id`` to
    prevent self-handoff.

    Attributes:
        from_agent_id: Agent that produced the artifact.
        to_agent_id: Agent that will consume the artifact.
        from_stage: Producing stage label (e.g. ``"generator"``).
        to_stage: Consuming stage label (e.g. ``"evaluator"``).
        payload: JSON-serializable feature spec and outputs.
        artifact_refs: IDs of artifacts in the artifact store.
        acceptance_probes: Atomic probes from criteria decomposition.
        rubric: Optional rubric included for evaluator handoffs.
    """

    from_agent_id: NotBlankStr = Field(
        description="Producing agent identifier",
    )
    to_agent_id: NotBlankStr = Field(
        description="Consuming agent identifier",
    )
    from_stage: NotBlankStr = Field(description="Producing stage label")
    to_stage: NotBlankStr = Field(description="Consuming stage label")
    payload: Mapping[str, object] = Field(
        default_factory=dict,
        description="JSON-serializable payload",
    )
    artifact_refs: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Artifact store reference IDs",
    )
    acceptance_probes: tuple[AtomicProbe, ...] = Field(
        default=(),
        description="Atomic probes from criteria decomposition",
    )
    rubric: VerificationRubric | None = Field(
        default=None,
        description="Rubric for evaluator handoffs",
    )

    @field_validator("payload", mode="before")
    @classmethod
    def _deepcopy_payload(cls, v: object) -> object:
        """Deep-copy mutable mapping payloads to prevent aliasing.

        Returns:
            A deep copy of ``v`` when it is a mapping; otherwise
            ``v`` unchanged.
        """
        if isinstance(v, Mapping):
            return copy.deepcopy(v)
        return v

    @model_validator(mode="after")
    def _reject_self_handoff(self) -> Self:
        """Reject handoffs where sender and receiver are the same.

        Returns:
            ``self`` unchanged when sender and receiver differ and
            the payload is JSON-serialisable.

        Raises:
            ValueError: When sender and receiver match, or when the
                payload cannot be JSON-serialised.
        """
        if self.from_agent_id == self.to_agent_id:
            msg = "Self-handoff rejected: from_agent_id must differ from to_agent_id"
            raise ValueError(msg)
        try:
            json.dumps(dict(self.payload), allow_nan=False)
        except (TypeError, ValueError) as exc:
            msg = f"Payload must be JSON-serializable: {safe_error_description(exc)}"
            raise ValueError(msg) from exc
        return self
