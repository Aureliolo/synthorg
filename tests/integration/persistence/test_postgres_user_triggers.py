"""Postgres-only integration tests for user invariant triggers.

Parallel to ``test_user_triggers.py`` which covers the SQLite backend,
this module runs the same trigger semantics against a real Postgres 18
container to verify Postgres CONSTRAINT TRIGGER parity with SQLite's
BEFORE UPDATE triggers.

Requires Docker (via testcontainers).  Skipped when Docker is absent.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest

from synthorg.core.auth.models import OrgRole, User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend


def _make_user(
    *,
    user_id: str = "user-001",
    username: str = "alice",
    role: HumanRole = HumanRole.MANAGER,
    org_roles: tuple[OrgRole, ...] = (),
) -> User:
    now = datetime.now(UTC)
    return User(
        id=user_id,
        username=username,
        password_hash="$argon2id$fake-hash",
        role=role,
        must_change_password=False,
        org_roles=org_roles,
        created_at=now,
        updated_at=now,
    )


# ── CEO uniqueness (unique partial index) ───────────────────────


@pytest.mark.integration
class TestCEOUniquenessPostgres:
    """Unique partial index prevents two CEOs on Postgres."""

    async def test_second_ceo_rejected(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        ceo1 = _make_user(user_id="ceo-1", username="ceo1", role=HumanRole.CEO)
        await postgres_backend.users.save(ceo1)

        ceo2 = _make_user(user_id="ceo-2", username="ceo2", role=HumanRole.CEO)
        with pytest.raises(ConstraintViolationError) as exc_info:
            await postgres_backend.users.save(ceo2)
        assert exc_info.value.constraint == "idx_single_ceo"

    async def test_concurrent_ceo_creation(
        self,
        postgres_backend: PostgresPersistenceBackend,
        postgres_backend_factory: Callable[[], Awaitable[PostgresPersistenceBackend]],
    ) -> None:
        """5 concurrent CEO inserts split across two pools.

        Three writers run through the original ``postgres_backend`` pool,
        two run through a second backend instance bound to the same DSN
        but with its own pool.  A same-pool race can hide behind
        serialisation quirks (prepared-statement cache, channel
        pinning); splitting writers between independent pools forces
        the DB itself to enforce the unique partial index.
        """
        backend_b = await postgres_backend_factory()
        results: list[bool] = []

        async def try_create(
            idx: int,
            backend: PostgresPersistenceBackend,
        ) -> None:
            user = _make_user(
                user_id=f"ceo-{idx}",
                username=f"ceo{idx}",
                role=HumanRole.CEO,
            )
            try:
                await backend.users.save(user)
                results.append(True)
            except ConstraintViolationError:
                results.append(False)

        async with asyncio.TaskGroup() as tg:
            for i in range(3):
                _ = tg.create_task(try_create(i, postgres_backend))
            for i in range(3, 5):
                _ = tg.create_task(try_create(i, backend_b))

        assert len(results) == 5, f"Expected 5 results, got {results}"
        assert sum(results) == 1, f"Expected exactly 1 success, got {results}"


# ── Last-CEO trigger (CONSTRAINT TRIGGER AFTER UPDATE) ──────────


@pytest.mark.integration
class TestLastCEOTriggerPostgres:
    """Postgres CONSTRAINT TRIGGER prevents removing the last CEO."""

    async def test_cannot_demote_only_ceo(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        ceo = _make_user(user_id="ceo-1", username="ceo1", role=HumanRole.CEO)
        await postgres_backend.users.save(ceo)

        demoted = ceo.model_copy(
            update={"role": HumanRole.MANAGER, "updated_at": datetime.now(UTC)},
        )
        with pytest.raises(ConstraintViolationError) as exc_info:
            await postgres_backend.users.save(demoted)
        assert exc_info.value.constraint == "enforce_ceo_minimum"


# ── Last-owner trigger (CONSTRAINT TRIGGER AFTER UPDATE OF org_roles) ──


@pytest.mark.integration
class TestLastOwnerTriggerPostgres:
    """Postgres CONSTRAINT TRIGGER prevents removing the last owner."""

    async def test_cannot_revoke_only_owner(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        owner = _make_user(
            user_id="owner-1",
            username="owner1",
            org_roles=(OrgRole.OWNER,),
        )
        await postgres_backend.users.save(owner)

        revoked = owner.model_copy(
            update={"org_roles": (), "updated_at": datetime.now(UTC)},
        )
        with pytest.raises(ConstraintViolationError) as exc_info:
            await postgres_backend.users.save(revoked)
        assert exc_info.value.constraint == "enforce_owner_minimum"

    async def test_can_revoke_owner_when_another_exists(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        owner1 = _make_user(
            user_id="owner-1",
            username="owner1",
            org_roles=(OrgRole.OWNER,),
        )
        owner2 = _make_user(
            user_id="owner-2",
            username="owner2",
            org_roles=(OrgRole.OWNER,),
        )
        await postgres_backend.users.save(owner1)
        await postgres_backend.users.save(owner2)

        # Revoke from owner1 -- owner2 still has it
        revoked = owner1.model_copy(
            update={"org_roles": (), "updated_at": datetime.now(UTC)},
        )
        await postgres_backend.users.save(revoked)  # should not raise

    async def test_concurrent_owner_revoke_one_fails(
        self,
        postgres_backend: PostgresPersistenceBackend,
        postgres_backend_factory: Callable[[], Awaitable[PostgresPersistenceBackend]],
    ) -> None:
        """Two concurrent owner revokes split across independent pools.

        Each writer uses its own ``PostgresPersistenceBackend``
        instance so the race is arbitrated by the CONSTRAINT TRIGGER,
        not by coincidentally shared pool state.
        """
        backend_b = await postgres_backend_factory()
        owner1 = _make_user(
            user_id="owner-1",
            username="owner1",
            org_roles=(OrgRole.OWNER,),
        )
        owner2 = _make_user(
            user_id="owner-2",
            username="owner2",
            org_roles=(OrgRole.OWNER,),
        )
        await postgres_backend.users.save(owner1)
        await postgres_backend.users.save(owner2)

        results: list[bool] = []

        async def try_revoke(
            user: User,
            backend: PostgresPersistenceBackend,
        ) -> None:
            revoked = user.model_copy(
                update={"org_roles": (), "updated_at": datetime.now(UTC)},
            )
            try:
                await backend.users.save(revoked)
                results.append(True)
            except ConstraintViolationError:
                results.append(False)

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(try_revoke(owner1, postgres_backend))
            _ = tg.create_task(try_revoke(owner2, backend_b))

        assert len(results) == 2, f"Expected 2 results, got {results}"
        assert sum(results) == 1, f"Expected exactly 1 success, got {results}"
