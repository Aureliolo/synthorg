"""User and ApiKey repository protocols.

Co-located because every API key belongs to a user (FK) and the two
repositories share the auth admin surface.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.auth.models import ApiKey, User  # noqa: TC001
from synthorg.core.auth.roles import HumanRole  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT


@runtime_checkable
class UserRepository(Protocol):
    """CRUD interface for User persistence."""

    async def save(self, user: User) -> None:
        """Persist a user (insert or update).

        Args:
            user: The user to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(self, user_id: NotBlankStr) -> User | None:
        """Retrieve a user by ID.

        Args:
            user_id: The user identifier.

        Returns:
            The user, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_by_username(self, username: NotBlankStr) -> User | None:
        """Retrieve a user by username.

        Args:
            username: The login username.

        Returns:
            The user, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_users(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[User, ...]:
        """List human users (excludes the system user).

        Bounded by ``limit`` so an unauth'd caller cannot materialise an
        unbounded tuple of users. For cursor-stable pagination across
        large user bases use :meth:`list_users_paginated` instead.

        Args:
            limit: Maximum users to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            Human users as a tuple, capped at *limit* rows.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_users_paginated(
        self,
        *,
        after_id: NotBlankStr | None,
        limit: int,
    ) -> tuple[User, ...]:
        """List a page of human users using keyset pagination on ``id``.

        Returns up to ``limit`` rows whose ``id > after_id`` (or all
        rows when ``after_id`` is ``None``).  The keyset contract is
        stable under concurrent inserts and deletes: new users
        beyond the cursor land on a later page; deletions can only
        shorten future pages, never duplicate or skip rows already
        seen.  Offset-based pagination cannot make that guarantee.

        ``id`` is the sort key (not ``created_at``) so the keyset is
        unique on every row, even on bulk imports that collide on the
        same timestamp.

        Args:
            after_id: Sort-key cursor.  ``None`` for the first page;
                the previous page's last ``id`` for follow-up pages.
            limit: Maximum rows to return.  Callers wanting an
                ``has_more`` signal should pass ``limit + 1`` and
                inspect the overflow.

        Returns:
            Page of users in ascending ``id`` order.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def count(self) -> int:
        """Count the number of human users (excludes the system user).

        Returns:
            Human user count.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def count_by_role(self, role: HumanRole) -> int:
        """Count users with a specific role.

        Args:
            role: The role to filter by.

        Returns:
            Number of users with the given role.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, user_id: NotBlankStr) -> bool:
        """Delete a user by ID.

        Args:
            user_id: The user identifier.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...


@runtime_checkable
class ApiKeyRepository(Protocol):
    """CRUD interface for API key persistence."""

    async def save(self, key: ApiKey) -> None:
        """Persist an API key.

        Args:
            key: The API key to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(self, key_id: NotBlankStr) -> ApiKey | None:
        """Retrieve an API key by ID.

        Args:
            key_id: The key identifier.

        Returns:
            The API key, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_by_hash(self, key_hash: NotBlankStr) -> ApiKey | None:
        """Retrieve an API key by its hash.

        Args:
            key_hash: HMAC-SHA256 hex digest.

        Returns:
            The API key, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_by_user(
        self,
        user_id: NotBlankStr,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        """List API keys belonging to a user, optionally paginated.

        Args:
            user_id: The owner user ID.
            limit: Maximum keys to return; ``None`` (default) preserves
                fetch-all semantics.
            offset: Keys to skip; applied independently of *limit*.
                ``offset > 0`` with ``limit=None`` yields a tail
                window starting after *offset* rows.

        Returns:
            API keys for the user.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, key_id: NotBlankStr) -> bool:
        """Delete an API key by ID.

        Args:
            key_id: The key identifier.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
