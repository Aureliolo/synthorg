"""In-memory user repository fake for API unit tests.

Enforces the same invariants as the real Postgres/SQLite backends:
unique username, at most one CEO, at least one CEO, at least one owner.
"""

import copy

from synthorg.api.auth.system_user import is_system_user
from synthorg.core.auth.models import OrgRole, User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.constraint_tokens import (
    IDX_SINGLE_CEO,
    LAST_CEO_TRIGGER,
    LAST_OWNER_TRIGGER,
    USERS_USERNAME_UNIQUE,
)
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.persistence._shared.pagination import (
    DEFAULT_LIST_LIMIT,
    validate_pagination_args,
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

    def seed(self, user: User) -> None:
        """Insert a user bypassing async ``save`` constraints.

        Tests sometimes need to pre-populate a target user from sync
        fixture helpers (where ``await`` is not available). Routing
        through this method keeps the internal storage encapsulated:
        callers do not poke at the underlying dict. The defensive
        deepcopy matches ``save()`` / ``get()`` so a later mutation of
        the caller's ``user`` object cannot bleed into repository
        state.
        """
        self._users[user.id] = copy.deepcopy(user)

    async def save(self, entity: User) -> None:
        existing = self._users.get(entity.id)
        # Username uniqueness
        for u in self._users.values():
            if u.username == entity.username and u.id != entity.id:
                msg = "UNIQUE constraint failed: users.username"
                raise ConstraintViolationError(msg, constraint=USERS_USERNAME_UNIQUE)
        # CEO uniqueness (partial unique index on role='ceo')
        if entity.role == HumanRole.CEO:
            for u in self._users.values():
                if u.role == HumanRole.CEO and u.id != entity.id:
                    msg = "UNIQUE constraint failed: idx_single_ceo"
                    raise ConstraintViolationError(
                        msg,
                        constraint=IDX_SINGLE_CEO,
                    )
        # Last-CEO trigger: prevent demoting the only CEO
        if (
            existing is not None
            and existing.role == HumanRole.CEO
            and entity.role != HumanRole.CEO
        ):
            other_ceos = sum(
                1
                for u in self._users.values()
                if u.role == HumanRole.CEO and u.id != entity.id
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
            and OrgRole.OWNER not in entity.org_roles
        ):
            other_owners = sum(
                1
                for u in self._users.values()
                if u.id != entity.id and OrgRole.OWNER in u.org_roles
            )
            if other_owners == 0:
                msg = "Cannot remove the last owner"
                raise ConstraintViolationError(
                    msg,
                    constraint=LAST_OWNER_TRIGGER,
                )
        self._users[entity.id] = copy.deepcopy(entity)

    async def get(self, entity_id: str) -> User | None:
        user = self._users.get(entity_id)
        return copy.deepcopy(user) if user is not None else None

    async def get_by_username(self, username: str) -> User | None:
        for user in self._users.values():
            if user.username == username:
                return copy.deepcopy(user)
        return None

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[User, ...]:
        limit = validate_pagination_args(limit, offset=offset, event="fake.list_items")
        humans = sorted(
            (u for u in self._users.values() if u.role != HumanRole.SYSTEM),
            key=lambda u: u.id,
        )
        sliced = humans[offset : offset + limit]
        return tuple(copy.deepcopy(u) for u in sliced)

    async def list_after_id(
        self,
        *,
        after_id: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[User, ...]:
        limit = validate_pagination_args(limit, offset=0, event="fake.list_after_id")
        humans = sorted(
            (u for u in self._users.values() if u.role != HumanRole.SYSTEM),
            key=lambda u: u.id,
        )
        if after_id is not None:
            humans = [u for u in humans if u.id > after_id]
        return tuple(copy.deepcopy(u) for u in humans[:limit])

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[User, ...]:
        humans = sorted(
            (u for u in self._users.values() if u.role != HumanRole.SYSTEM),
            key=lambda u: u.id,
        )
        role = getattr(filter_spec, "role", None)
        if role is not None:
            humans = [u for u in humans if u.role == role]
        return tuple(copy.deepcopy(u) for u in humans[offset : offset + limit])

    async def count(self, filter_spec: object | None = None) -> int:
        humans = [u for u in self._users.values() if u.role != HumanRole.SYSTEM]
        role = getattr(filter_spec, "role", None) if filter_spec is not None else None
        if role is not None:
            humans = [u for u in humans if u.role == role]
        return len(humans)

    async def count_by_role(self, role: HumanRole) -> int:
        return sum(1 for u in self._users.values() if u.role == role)

    async def delete(self, entity_id: str) -> bool:
        if is_system_user(entity_id):
            msg = "System user cannot be deleted"
            raise QueryError(msg)
        user = self._users.get(entity_id)
        if user is None:
            return False
        if user.role == HumanRole.CEO:
            other_ceos = sum(
                1
                for u in self._users.values()
                if u.role == HumanRole.CEO and u.id != entity_id
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
                if u.id != entity_id and OrgRole.OWNER in u.org_roles
            )
            if other_owners == 0:
                msg = "Cannot remove the last owner"
                raise ConstraintViolationError(
                    msg,
                    constraint=LAST_OWNER_TRIGGER,
                )
        del self._users[entity_id]
        return True
