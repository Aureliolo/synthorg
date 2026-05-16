"""User and ApiKey repository protocols.

Co-located because every API key belongs to a user (FK) and the two
repositories share the auth admin surface.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.auth.models import ApiKey, User
from synthorg.core.auth.roles import HumanRole  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)


class UserFilterSpec(BaseModel):
    """Filter spec for ``UserRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    role: HumanRole | None = Field(
        default=None,
        description="Filter by user role",
    )


class ApiKeyFilterSpec(BaseModel):
    """Filter spec for ``ApiKeyRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    user_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by owner user ID",
    )
    revoked_only: bool = Field(
        default=False,
        description="If True, return only revoked keys; if False, return all keys",
    )


@runtime_checkable
class UserRepository(
    IdKeyedRepository[User, NotBlankStr],
    FilteredQueryRepository[User, UserFilterSpec],
    Protocol,
):
    """CRUD + query interface for User persistence.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). Bespoke methods kept per D7:
    - ``get_by_username``: alternate-key lookup on indexed username column
    - ``count_by_role``: domain invariant (callers need role-count aggregate)
    """

    async def save(self, entity: User) -> None:
        """Persist a user (insert or update by id).

        Args:
            entity: The user to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(self, entity_id: NotBlankStr) -> User | None:
        """Retrieve a user by its ID.

        Args:
            entity_id: The user identifier.

        Returns:
            The user, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_by_username(self, username: NotBlankStr) -> User | None:
        """Retrieve a user by username (D7: alternate-key performance).

        Args:
            username: The login username.

        Returns:
            The user, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[User, ...]:
        """List human users (excludes the system user) with pagination.

        Args:
            limit: Maximum users to return.
            offset: Rows to skip before the window.

        Returns:
            Human users ordered by id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def query(
        self,
        filter_spec: UserFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[User, ...]:
        """List users matching the filter spec.

        Args:
            filter_spec: Carries optional filter for role.
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Matching users ordered by id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def count(self, filter_spec: UserFilterSpec) -> int:
        """Count users matching the filter spec.

        Args:
            filter_spec: Carries optional filter for role.

        Returns:
            Total number of matching users.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def count_by_role(self, role: HumanRole) -> int:
        """Count users with a specific role (D7: domain invariant).

        Args:
            role: The role to filter by.

        Returns:
            Number of users with the given role.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a user by ID.

        Args:
            entity_id: The user identifier.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...


@runtime_checkable
class ApiKeyRepository(
    IdKeyedRepository[ApiKey, NotBlankStr],
    FilteredQueryRepository[ApiKey, ApiKeyFilterSpec],
    Protocol,
):
    """CRUD + query interface for API key persistence.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). Bespoke method kept per D7:
    - ``get_by_hash``: alternate-key lookup on indexed key_hash column
    """

    async def save(self, entity: ApiKey) -> None:
        """Persist an API key (insert or update by id).

        Args:
            entity: The API key to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(self, entity_id: NotBlankStr) -> ApiKey | None:
        """Retrieve an API key by its ID.

        Args:
            entity_id: The key identifier.

        Returns:
            The API key, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_by_hash(self, key_hash: NotBlankStr) -> ApiKey | None:
        """Retrieve an API key by its hash (D7: alternate-key performance).

        Args:
            key_hash: HMAC-SHA256 hex digest.

        Returns:
            The API key, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        """List API keys with pagination.

        Args:
            limit: Maximum keys to return.
            offset: Rows to skip before the window.

        Returns:
            API keys ordered by id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def query(
        self,
        filter_spec: ApiKeyFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        """List API keys matching the filter spec.

        Args:
            filter_spec: Carries optional filters for user_id and revoked_only.
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Matching API keys ordered by id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def count(self, filter_spec: ApiKeyFilterSpec) -> int:
        """Count API keys matching the filter spec.

        Args:
            filter_spec: Carries optional filters.

        Returns:
            Total number of matching API keys.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete an API key by ID.

        Args:
            entity_id: The key identifier.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
