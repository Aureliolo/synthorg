"""Altitude-specific change models for the meta-loop.

Rollback operations / plans and the per-altitude change payloads
(config, architecture, prompt, code) that an
:class:`ImprovementProposal` carries. ``CodeChange`` enforces a
content-shape invariant matched to its :class:`CodeOperation`.
"""

from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from synthorg.core.types import NotBlankStr
from synthorg.meta._model_enums import CodeOperation, EvolutionMode


class RollbackOperation(BaseModel):
    """A single inverse operation in a rollback plan.

    Attributes:
        operation_type: Kind of reversal (revert_config, delete_role, etc.).
        target: What to revert (config path, role name, etc.).
        previous_value: Value to restore (None for deletions).
        description: Human-readable description.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    operation_type: NotBlankStr
    target: NotBlankStr
    previous_value: JsonValue = None
    description: NotBlankStr


class RollbackPlan(BaseModel):
    """Concrete plan for reverting an improvement proposal.

    Attributes:
        operations: Ordered inverse operations.
        dependencies: Proposal IDs that must rollback first.
        validation_check: Post-rollback assertion description.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    operations: tuple[RollbackOperation, ...] = Field(min_length=1)
    dependencies: tuple[UUID, ...] = ()
    validation_check: NotBlankStr


class ConfigChange(BaseModel):
    """A single config field change.

    Attributes:
        path: JSON-path to the config field (e.g. ``budget.total_monthly``).
        old_value: Current value.
        new_value: Proposed value.
        description: Why this change is proposed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    path: NotBlankStr
    old_value: JsonValue = None
    new_value: JsonValue = None
    description: NotBlankStr


class ArchitectureChange(BaseModel):
    """A structural change to the organization.

    Attributes:
        operation: Type of change (create_role, create_department,
            modify_workflow, remove_role, etc.).
        target_name: Name of the entity being changed.
        payload: Structured change data (operation-specific).
        description: Why this change is proposed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    operation: NotBlankStr
    target_name: NotBlankStr
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    description: NotBlankStr


class PromptChange(BaseModel):
    """An org-wide prompt policy change.

    Attributes:
        principle_text: The constitutional principle to inject.
        target_scope: Who this applies to (role name, department, or ``all``).
        evolution_mode: How this interacts with per-agent evolution.
        description: Why this change is proposed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    principle_text: NotBlankStr
    target_scope: NotBlankStr
    evolution_mode: EvolutionMode = EvolutionMode.ORG_WIDE
    description: NotBlankStr


class CodeChange(BaseModel):
    """A proposed change to a framework source file.

    Uses full file content rather than line-level diffs: LLMs produce
    complete content reliably, framework files are < 800 lines by
    convention, and git shows the actual diff on the PR.

    Attributes:
        file_path: Relative path from project root.
        operation: Type of file change (create, modify, delete).
        old_content: Current file content (empty for create; captured
            at proposal time for rollback on modify/delete).
        new_content: Proposed file content (empty for delete).
        description: What this change does.
        reasoning: Why this change improves the system.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    file_path: NotBlankStr
    operation: CodeOperation
    old_content: str = ""
    new_content: str = ""
    description: NotBlankStr
    reasoning: NotBlankStr

    @model_validator(mode="after")
    def _validate_content_for_operation(self) -> Self:
        """Ensure content fields match the operation type.

        Returns:
            ``Self`` instance.
        """
        _CODE_CHANGE_VALIDATORS[self.operation](self)
        return self


def _validate_create(change: CodeChange) -> None:
    """Validate create.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    if change.old_content:
        msg = "create operations must have empty old_content"
        raise ValueError(msg)
    if not change.new_content:
        msg = "create operations must have non-empty new_content"
        raise ValueError(msg)


def _validate_modify(change: CodeChange) -> None:
    """Validate modify.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    if not change.old_content:
        msg = "modify operations must have non-empty old_content"
        raise ValueError(msg)
    if not change.new_content:
        msg = "modify operations must have non-empty new_content"
        raise ValueError(msg)
    if change.old_content == change.new_content:
        msg = "modify operations must change the content"
        raise ValueError(msg)


def _validate_delete(change: CodeChange) -> None:
    """Validate delete.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    if not change.old_content:
        msg = "delete operations must have non-empty old_content"
        raise ValueError(msg)
    if change.new_content:
        msg = "delete operations must have empty new_content"
        raise ValueError(msg)


_CODE_CHANGE_VALIDATORS = {
    CodeOperation.CREATE: _validate_create,
    CodeOperation.MODIFY: _validate_modify,
    CodeOperation.DELETE: _validate_delete,
}
