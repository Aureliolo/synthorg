"""Policy engine domain models."""

import copy
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


class PolicyActionRequest(BaseModel):
    """Structured action request for policy evaluation.

    Attributes:
        action_type: Semantic action key (e.g. ``"tool_invoke"``,
            ``"delegation"``, ``"approval_execute"``).
        principal: Agent ID performing the action.
        resource: Target of the action (tool name, delegation
            target, or approval ID).
        context: Additional key-value context (task_id, risk_level,
            autonomy level, etc.).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action_type: NotBlankStr = Field(
        description="Semantic action key",
    )
    principal: NotBlankStr = Field(
        description="Agent ID performing the action",
    )
    resource: NotBlankStr = Field(
        description="Target of the action",
    )
    context: Mapping[str, Any] = Field(
        default_factory=dict,
        description="Additional evaluation context",
    )

    @model_validator(mode="after")
    def _deep_copy_context(self) -> Self:
        """Deep-copy and recursively freeze context."""
        object.__setattr__(
            self,
            "context",
            _recursive_freeze(copy.deepcopy(dict(self.context))),
        )
        return self


def _recursive_freeze(obj: object) -> object:
    """Recursively freeze mutable containers."""
    if isinstance(obj, dict):
        return MappingProxyType(
            {k: _recursive_freeze(v) for k, v in obj.items()},
        )
    if isinstance(obj, list):
        return tuple(_recursive_freeze(v) for v in obj)
    if isinstance(obj, set):
        return frozenset(_recursive_freeze(v) for v in obj)
    return obj


class PolicyDecision(BaseModel):
    """Result of a policy evaluation.

    Attributes:
        allow: Whether the action is permitted.
        reason: Human-readable explanation.
        matched_policy: Name of the policy that matched, if any.
        latency_ms: Time taken for evaluation in milliseconds.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    allow: bool = Field(description="Whether the action is permitted")
    reason: NotBlankStr = Field(description="Human-readable explanation")
    matched_policy: NotBlankStr | None = Field(
        default=None,
        description="Policy that matched",
    )
    latency_ms: float = Field(
        ge=0.0,
        description="Evaluation latency in milliseconds",
    )
