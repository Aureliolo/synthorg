"""In-memory user repository fake for API unit tests.

Enforces the same invariants as the real Postgres/SQLite backends:
unique username, at most one CEO, at least one CEO, at least one owner.
"""

import copy

from synthorg.api.auth.models import OrgRole, User
from synthorg.api.auth.system_user import is_system_user
from synthorg.api.guards import HumanRole
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.constraint_tokens import (
    IDX_SINGLE_CEO,
    LAST_CEO_TRIGGER,
    LAST_OWNER_TRIGGER,
    USERS_USERNAME_UNIQUE,
)


class FakeUserRepository:
    """In-memory user repository for tests.

    Enforces the same constraints as the real DB:
    - Unique username
    - At most one CEO (unique partial index)
    - At least one CEO (trigger on role change)
    - At least one owner (trigger on org_roles change)
    """

    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    async def save(self, user: User) -> None:
        existing = self._users.get(user.id)
        # Username uniqueness
        for u in self._users.values():
            if u.username == user.username and u.id != user.id:
                msg = "UNIQUE constraint failed: users.username"
                raise ConstraintViolationError(msg, constraint=USERS_USERNAME_UNIQUE)
        # CEO uniqueness (partial unique index on role='ceo')
        if user.role == HumanRole.CEO:
            for u in self._users.values():
                if u.role == HumanRole.CEO and u.id != user.id:
                    msg = "UNIQUE constraint failed: idx_single_ceo"
                    raise ConstraintViolationError(
                        msg,
                        constraint=IDX_SINGLE_CEO,
                    )
        # Last-CEO trigger: prevent demoting the only CEO
        if (
            existing is not None
            and existing.role == HumanRole.CEO
            and user.role != HumanRole.CEO
        ):
            other_ceos = sum(
                1
                for u in self._users.values()
                if u.role == HumanRole.CEO and u.id != user.id
            )
            if other_ceos == 0:
                msg = "Cannot remove the last CEO"
                raise ConstraintViolationError(
                    msg,
                    constraint=LAST_CEO_TRIGGER,
                )
        # Last-owner trigger: prevent removing the last owner
        if (
            existing is not None
            and OrgRole.OWNER in existing.org_roles
            and OrgRole.OWNER not in user.org_roles
        ):
            other_owners = sum(
                1
                for u in self._users.values()
                if u.id != user.id and OrgRole.OWNER in u.org_roles
            )
            if other_owners == 0:
                msg = "Cannot remove the last owner"
                raise ConstraintViolationError(
                    msg,
                    constraint=LAST_OWNER_TRIGGER,
                )
        self._users[user.id] = copy.deepcopy(user)

    async def get(self, user_id: str) -> User | None:
        user = self._users.get(user_id)
        return copy.deepcopy(user) if user is not None else None

    async def get_by_username(self, username: str) -> User | None:
        for user in self._users.values():
            if user.username == username:
                return copy.deepcopy(user)
        return None

    async def list_users(self) -> tuple[User, ...]:
        return tuple(
            copy.deepcopy(u) for u in self._users.values() if u.role != HumanRole.SYSTEM
        )

    async def list_users_paginated(
        self,
        *,
        after_id: NotBlankStr | None,
        limit: int,
    ) -> tuple[User, ...]:
        all_humans = sorted(
            (u for u in self._users.values() if u.role != HumanRole.SYSTEM),
            key=lambda u: u.id,
        )
        if after_id is not None:
            all_humans = [u for u in all_humans if u.id > after_id]
        return tuple(copy.deepcopy(u) for u in all_humans[:limit])

    async def count(self) -> int:
        return sum(1 for u in self._users.values() if u.role != HumanRole.SYSTEM)

    async def count_by_role(self, role: HumanRole) -> int:
        return sum(1 for u in self._users.values() if u.role == role)

    async def delete(self, user_id: str) -> bool:
        if is_system_user(user_id):
            msg = "System user cannot be deleted"
            raise QueryError(msg)
        user = self._users.get(user_id)
        if user is None:
            return False
        if user.role == HumanRole.CEO:
            other_ceos = sum(
                1
                for u in self._users.values()
                if u.role == HumanRole.CEO and u.id != user_id
            )
            if other_ceos == 0:
                msg = "Cannot remove the last CEO"
                raise ConstraintViolationError(
                    msg,
                    constraint=LAST_CEO_TRIGGER,
                )
        if OrgRole.OWNER in user.org_roles:
            other_owners = sum(
                1
                for u in self._users.values()
                if u.id != user_id and OrgRole.OWNER in u.org_roles
            )
            if other_owners == 0:
                msg = "Cannot remove the last owner"
                raise ConstraintViolationError(
                    msg,
                    constraint=LAST_OWNER_TRIGGER,
                )
        del self._users[user_id]
        return True
