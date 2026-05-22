"""Domain models for the self-extending toolkit (toolsmith).

Defines the capability-gap signal, the authored-tool blueprint and its
lifecycle state, and the benchmark-validation result that gates a
blueprint from ``VALIDATED`` to ``ACTIVE``.
"""

import itertools
import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

if TYPE_CHECKING:
    from datetime import datetime

# Authored tools reuse the MCP tool-surface contract verbatim so a
# blueprint can be promoted into an ``MCPToolDef`` without re-validation
# drift: the name follows ``synthorg_{domain}_{action}`` and the
# capability follows ``domain:action``.
_TOOL_NAME_RE = re.compile(r"^synthorg_[a-z][a-z0-9_]*_[a-z][a-z0-9_]*$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")
_ACTION_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")

# Authored scripts are bounded so a malformed or runaway authoring call
# cannot persist an unbounded blob or exhaust the sandbox at invoke time.
_MAX_SCRIPT_BODY_CHARS: Final[int] = 65536


class ToolBlueprintState(StrEnum):
    """Lifecycle state of an authored tool blueprint.

    A blueprint is born ``PENDING`` (proposed, not yet trusted), moves to
    ``VALIDATED`` once the benchmark gate passes, ``ACTIVE`` once it is
    live-registered in the dynamic registry, and ``RETIRED`` when rolled
    back. Transitions are atomic compare-and-set at the persistence layer.
    """

    PENDING = "pending"
    VALIDATED = "validated"
    ACTIVE = "active"
    RETIRED = "retired"


class ToolSandboxBackend(StrEnum):
    """Sandbox backend an authored tool's script runs in.

    ``DOCKER`` is the default (container isolation, no network unless the
    blueprint opts in); ``SUBPROCESS`` is an opt-in lighter backend for
    trusted, fast scripts.
    """

    DOCKER = "docker"
    SUBPROCESS = "subprocess"


class CapabilityGap(BaseModel):
    """An aggregated, recurring capability gap.

    Attributes:
        signature: Stable key identifying the missing capability
            (typically the requested ``domain:action`` capability tag).
        occurrences: Number of times the gap was observed in the window.
        first_seen: First observation timestamp (UTC).
        last_seen: Most recent observation timestamp (UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    signature: NotBlankStr
    occurrences: int = Field(ge=1)
    first_seen: AwareDatetime
    last_seen: AwareDatetime

    @model_validator(mode="after")
    def _ordering(self) -> Self:
        """Enforce ``first_seen <= last_seen``."""
        if self.first_seen > self.last_seen:
            msg = "first_seen must be <= last_seen"
            raise ValueError(msg)
        return self


class ToolValidationResult(BaseModel):
    """Outcome of running the benchmark gate against a tool blueprint.

    The gate passes iff the focused per-tool acceptance brief passes AND
    the golden-company scorecard with the candidate tool registered does
    not regress against the baseline (``candidate_score >= baseline_score``).

    Attributes:
        passed: Whether the blueprint is trusted (both checks passed).
        brief_passed: Whether the per-tool acceptance brief passed.
        brief_score: Per-tool acceptance brief score in ``[0, 100]``.
        baseline_score: Golden scorecard total without the candidate.
        candidate_score: Golden scorecard total with the candidate.
        margin: ``candidate_score - baseline_score`` (may be negative).
        detail: Human-readable summary of the gate decision.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    passed: bool
    brief_passed: bool
    brief_score: int = Field(ge=0, le=100)
    baseline_score: int = Field(ge=0)
    candidate_score: int = Field(ge=0)
    margin: int
    detail: NotBlankStr

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        """Enforce margin arithmetic and the pass predicate."""
        expected_margin = self.candidate_score - self.baseline_score
        if self.margin != expected_margin:
            msg = (
                f"margin={self.margin} does not match "
                f"candidate_score - baseline_score (={expected_margin})"
            )
            raise ValueError(msg)
        # ``passed`` implies (but is not implied by) a passing brief and a
        # non-regressing margin: the gate may additionally require a
        # positive ``min_score_margin``, so an eligible result can still
        # be ``passed=False``. What is never allowed is passing while the
        # brief failed or the scorecard regressed.
        if self.passed and not (self.brief_passed and self.margin >= 0):
            msg = (
                f"passed=True contradicts brief_passed={self.brief_passed} "
                f"and margin={self.margin}"
            )
            raise ValueError(msg)
        return self


class ToolBlueprint(BaseModel):
    """A runtime-authored tool: declarative spec plus a sandbox script body.

    A blueprint is the persisted, governed unit of the self-extending
    toolkit. Once trusted and activated, it is promoted to an
    ``MCPToolDef`` (name, capability, parameters schema) backed by a
    per-tool handler that runs ``script_body`` in the resolved sandbox.

    Attributes:
        id: Stable blueprint identifier (primary key).
        name: MCP tool name (``synthorg_{domain}_{action}``).
        description: Human-readable description for LLM prompts.
        capability: Capability tag in ``domain:action`` format.
        parameters_schema: JSON Schema describing the tool's inputs. A
            real Pydantic ``args_model`` is materialised from this at
            registration time so dynamic tools keep the same typed
            validation symmetry as static tools.
        script_body: Source the sandbox executes for each invocation.
        sandbox_backend: Backend the script runs in (Docker by default).
        requires_network: Whether the sandbox is granted network egress
            (default ``False``).
        action_type: Permission-classification action in ``category:action``
            format (e.g. ``"code:read"``), gating the tool through autonomy.
        state: Lifecycle state.
        created_at: Creation timestamp (UTC).
        validated_at: When the benchmark gate passed (UTC), else ``None``.
        activated_at: When the tool was live-registered (UTC), else ``None``.
        retired_at: When the tool was rolled back (UTC), else ``None``.
        validation: Benchmark-gate result, populated once validated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    name: NotBlankStr
    description: NotBlankStr
    capability: NotBlankStr
    parameters_schema: dict[str, Any]
    script_body: NotBlankStr = Field(max_length=_MAX_SCRIPT_BODY_CHARS)
    sandbox_backend: ToolSandboxBackend = ToolSandboxBackend.DOCKER
    requires_network: bool = False
    action_type: NotBlankStr
    state: ToolBlueprintState = ToolBlueprintState.PENDING
    created_at: AwareDatetime
    validated_at: AwareDatetime | None = None
    activated_at: AwareDatetime | None = None
    retired_at: AwareDatetime | None = None
    validation: ToolValidationResult | None = None

    @model_validator(mode="after")
    def _validate_name_capability_action(self) -> Self:
        """Enforce the MCP naming + capability + action-type contracts.

        Beyond per-field format, the ``synthorg_{domain}_{action}`` name
        must denote the same ``{domain}/{action}`` as the ``{domain}:{action}``
        capability tag. A drift here would mean routing and governance see
        different identifiers for the same tool, which the LayeredHandlerMap
        cannot reconcile.
        """
        if not _TOOL_NAME_RE.match(self.name):
            msg = f"name must match 'synthorg_{{domain}}_{{action}}': {self.name!r}"
            raise ValueError(msg)
        if not _CAPABILITY_RE.match(self.capability):
            msg = f"capability must match 'domain:action': {self.capability!r}"
            raise ValueError(msg)
        if not _ACTION_TYPE_RE.match(self.action_type):
            msg = f"action_type must match 'category:action': {self.action_type!r}"
            raise ValueError(msg)
        capability_domain, capability_action = self.capability.split(":", 1)
        expected_name = f"synthorg_{capability_domain}_{capability_action}"
        if self.name != expected_name:
            msg = (
                "name and capability must reference the same domain/action: "
                f"name={self.name!r}, capability={self.capability!r}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_schema_shape(self) -> Self:
        """Require an object schema with a ``properties`` mapping.

        A materialisable ``args_model`` needs named properties; a schema
        without them cannot round-trip through ``pydantic.create_model``.
        """
        if self.parameters_schema.get("type") != "object":
            msg = "parameters_schema must declare 'type': 'object'"
            raise ValueError(msg)
        props = self.parameters_schema.get("properties")
        if not isinstance(props, dict):
            # Pydantic validators signal failure via ValueError, which the
            # framework converts to a ValidationError; TypeError would not
            # be caught the same way.
            msg = "parameters_schema must declare a 'properties' object"
            raise ValueError(msg)  # noqa: TRY004
        return self

    @model_validator(mode="after")
    def _validate_state_timestamps(self) -> Self:
        """Lifecycle timestamps must be present for their state, ordered.

        Any post-PENDING state must carry the validation record: that is
        the gate's audit evidence, and a missing record would mean a
        consumer could observe a "validated" blueprint with no proof the
        gate ever ran. The applier writes the record at the same instant
        as ``validated_at``, so the two are inseparable across lifecycle.
        """
        # Every post-PENDING state is gate-graduated, so validated_at and
        # the validation record are required throughout. A RETIRED tool
        # was rolled back AFTER activation, so it must also carry
        # activated_at; only PENDING and the impossible "retired without
        # ever activating" lack it. This mirrors the DB-layer lifecycle
        # CHECK so the model and persistence agree.
        terminal_states = {
            ToolBlueprintState.VALIDATED,
            ToolBlueprintState.ACTIVE,
            ToolBlueprintState.RETIRED,
        }
        activated_states = {ToolBlueprintState.ACTIVE, ToolBlueprintState.RETIRED}
        if self.state in terminal_states and self.validated_at is None:
            msg = f"validated_at required in state {self.state.value!r}"
            raise ValueError(msg)
        if self.state in terminal_states and self.validation is None:
            msg = f"validation result required in state {self.state.value!r}"
            raise ValueError(msg)
        if self.state in activated_states and self.activated_at is None:
            msg = f"activated_at required in state {self.state.value!r}"
            raise ValueError(msg)
        if self.state is ToolBlueprintState.RETIRED and self.retired_at is None:
            msg = "retired_at required in state 'retired'"
            raise ValueError(msg)
        self._assert_monotonic()
        return self

    def _assert_monotonic(self) -> None:
        """Enforce created <= validated <= activated <= retired ordering."""
        ordered: list[tuple[str, datetime | None]] = [
            ("created_at", self.created_at),
            ("validated_at", self.validated_at),
            ("activated_at", self.activated_at),
            ("retired_at", self.retired_at),
        ]
        seen = [(name, ts) for name, ts in ordered if ts is not None]
        for (prev_name, prev_ts), (name, ts) in itertools.pairwise(seen):
            if ts < prev_ts:
                msg = f"{name} must be >= {prev_name}"
                raise ValueError(msg)
