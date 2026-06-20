# module-kind: declarative
"""Durable role-registry record wrapping the domain :class:`Role`.

A :class:`RoleRecord` is the stored form of a first-class role in the durable
role registry. It wraps the immutable domain :class:`Role` with registry
metadata the role model itself does not carry: ``is_builtin`` (seeded from
``BUILTIN_ROLES`` so ``remove_role`` can refuse to delete a built-in) and the
create / update timestamps. The registry is keyed by ``role.name``.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.role import Role


class RoleRecord(BaseModel):
    """A role as stored in the durable registry.

    Attributes:
        role: The immutable domain role.
        is_builtin: ``True`` for a role seeded from ``BUILTIN_ROLES``.
        created_at: First-written timestamp (UTC-aware).
        updated_at: Last-refreshed timestamp (UTC-aware).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    role: Role
    is_builtin: bool = Field(default=False)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @property
    def name(self) -> str:
        """Return the registry key (the role name).

        Returns:
            The wrapped role's name.
        """
        return self.role.name


__all__ = ["RoleRecord"]
