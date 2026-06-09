"""Agent identity diff -- field-level changes between two AgentIdentity snapshots.

Compares ``model_dump(mode="json")`` representations recursively so nested
sub-models (e.g. ``personality.risk_tolerance``) produce dot-notation paths
like ``personality.risk_tolerance``.
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import NotBlankStr

__all__ = ["AgentIdentityDiff", "IdentityFieldChange", "compute_diff"]


ChangeType = Literal["modified", "added", "removed"]


class IdentityFieldChange(BaseModel):
    """A single field-level change between two identity versions.

    Attributes:
        field_path: Dot-notation path to the changed field (e.g.
            ``personality.risk_tolerance``).
        change_type: Whether the field was modified, added, or removed.
        old_value: JSON-serialized previous value, or ``None`` if added.
        new_value: JSON-serialized new value, or ``None`` if removed.

    Invariants:
        - ``"added"``: ``old_value`` must be ``None`` (there was no prior value).
        - ``"removed"``: ``new_value`` must be ``None`` (the field no longer exists).
        - ``"modified"``: both ``old_value`` and ``new_value`` must be non-``None``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    field_path: NotBlankStr
    change_type: ChangeType
    old_value: str | None = None
    new_value: str | None = None

    @model_validator(mode="after")
    def _validate_change_invariants(self) -> IdentityFieldChange:
        if self.change_type == "added":
            if self.old_value is not None:
                msg = "change_type='added' requires old_value=None"
                raise ValueError(msg)
            if self.new_value is None:
                msg = "change_type='added' requires new_value to be set"
                raise ValueError(msg)
        if self.change_type == "removed":
            if self.new_value is not None:
                msg = "change_type='removed' requires new_value=None"
                raise ValueError(msg)
            if self.old_value is None:
                msg = "change_type='removed' requires old_value to be set"
                raise ValueError(msg)
        if self.change_type == "modified" and (
            self.old_value is None or self.new_value is None
        ):
            msg = "change_type='modified' requires both old_value and new_value"
            raise ValueError(msg)
        return self


class AgentIdentityDiff(BaseModel):
    """Summary of all field-level changes between two identity versions.

    Attributes:
        agent_id: Agent whose identity changed.
        from_version: Version number of the older snapshot.
        to_version: Version number of the newer snapshot.
        field_changes: All detected field changes, ordered by field path.
        summary: Human-readable one-line summary (e.g. ``"2 fields changed"``),
            derived from ``field_changes``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    field_changes: tuple[IdentityFieldChange, ...] = ()

    @model_validator(mode="after")
    def _validate_version_order(self) -> AgentIdentityDiff:
        if self.from_version >= self.to_version:
            msg = (
                f"from_version must be less than to_version "
                f"(from_version={self.from_version}, "
                f"to_version={self.to_version})"
            )
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> str:
        """Human-readable one-line summary of the diff."""
        n = len(self.field_changes)
        if n == 0:
            return "no changes"
        if n == 1:
            return "1 field changed"
        return f"{n} fields changed"


def _serialize(value: object) -> str:
    """Return a compact JSON string for a leaf value."""
    return json.dumps(value, sort_keys=True, default=str)


def _diff_dicts(
    old: dict[str, object],
    new: dict[str, object],
    prefix: str,
    changes: list[IdentityFieldChange],
) -> None:
    """Recursively compare two dicts and append changes to *changes*.

    Args:
        old: JSON-serializable dict from the older model dump.
        new: JSON-serializable dict from the newer model dump.
        prefix: Dot-notation path prefix for nested fields.
        changes: Accumulator list -- new changes are appended in-place.
    """
    all_keys = old.keys() | new.keys()
    for key in sorted(all_keys):
        path = f"{prefix}.{key}" if prefix else key
        if key not in old:
            changes.append(
                IdentityFieldChange(
                    field_path=path,
                    change_type="added",
                    old_value=None,
                    new_value=_serialize(new[key]),
                )
            )
        elif key not in new:
            changes.append(
                IdentityFieldChange(
                    field_path=path,
                    change_type="removed",
                    old_value=_serialize(old[key]),
                    new_value=None,
                )
            )
        else:
            old_value = old[key]
            new_value = new[key]
            if isinstance(old_value, dict) and isinstance(new_value, dict):
                _diff_dicts(old_value, new_value, path, changes)
            elif old_value != new_value:
                changes.append(
                    IdentityFieldChange(
                        field_path=path,
                        change_type="modified",
                        old_value=_serialize(old_value),
                        new_value=_serialize(new_value),
                    )
                )


def compute_diff(
    agent_id: NotBlankStr,
    old_snapshot: BaseModel,
    new_snapshot: BaseModel,
    from_version: int,
    to_version: int,
) -> AgentIdentityDiff:
    """Compute the field-level diff between two identity snapshots.

    Args:
        agent_id: Agent whose identity changed.
        old_snapshot: The older ``AgentIdentity`` (or any frozen Pydantic model).
        new_snapshot: The newer ``AgentIdentity``.
        from_version: Version number of the older snapshot.
        to_version: Version number of the newer snapshot.

    Returns:
        An :class:`AgentIdentityDiff` listing every changed field.
    """
    old_dict: dict[str, object] = old_snapshot.model_dump(mode="json")
    new_dict: dict[str, object] = new_snapshot.model_dump(mode="json")

    changes: list[IdentityFieldChange] = []
    _diff_dicts(old_dict, new_dict, "", changes)

    return AgentIdentityDiff(
        agent_id=agent_id,
        from_version=from_version,
        to_version=to_version,
        field_changes=tuple(changes),
    )
