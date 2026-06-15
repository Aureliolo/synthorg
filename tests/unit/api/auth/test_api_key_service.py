"""Unit tests for ApiKeyService (issue / list / revoke).

API-key issuance and revocation must emit signed ``security.api_key.*``
audit events after the persistence write, enforce a role ceiling at
issuance, and gate revocation to the owner (or a CEO) with a 404 (never
403) for non-owners.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.api.auth.api_key_service import ApiKeyService
from synthorg.api.auth.service import AuthService
from synthorg.core.auth.config import AuthConfig
from synthorg.core.auth.models import ApiKey, AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import ApiKeyNotFoundError, ForbiddenError
from synthorg.persistence.user_protocol import ApiKeyFilterSpec
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_SECRET = "test-secret-key-must-be-32-chars-long!"
_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


class _FakeApiKeyRepo:
    """Minimal in-memory ApiKeyRepository for service tests."""

    def __init__(self) -> None:
        self._store: dict[str, ApiKey] = {}

    async def save(self, entity: ApiKey, /) -> None:
        self._store[entity.id] = entity

    async def get(self, entity_id: str, /) -> ApiKey | None:
        return self._store.get(entity_id)

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        return next((k for k in self._store.values() if k.key_hash == key_hash), None)

    async def query(
        self,
        filter_spec: ApiKeyFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        rows = [
            k
            for k in self._store.values()
            if filter_spec.user_id is None or k.user_id == filter_spec.user_id
        ]
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: ApiKeyFilterSpec) -> int:
        return len(await self.query(filter_spec))

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ApiKey, ...]:
        return tuple(list(self._store.values())[offset : offset + limit])

    async def delete(self, entity_id: str, /) -> bool:
        return self._store.pop(entity_id, None) is not None


def _service(repo: _FakeApiKeyRepo) -> ApiKeyService:
    return ApiKeyService(
        api_keys=repo,
        auth_service=AuthService(AuthConfig(jwt_secret=_SECRET)),
        clock=FakeClock(start=_NOW),
    )


def _user(
    *, user_id: str = "u-owner", role: HumanRole = HumanRole.MANAGER
) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        username="alice",
        role=role,
        auth_method=AuthMethod.JWT,
    )


async def test_issue_persists_hashed_key_and_returns_plaintext_once() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    issued = await svc.issue(owner=_user(), name="ci", role=HumanRole.OBSERVER)

    # Plaintext returned once; only the hash is persisted.
    assert issued.plaintext
    stored = await repo.get(issued.view.id)
    assert stored is not None
    assert stored.key_hash != issued.plaintext
    assert stored.user_id == "u-owner"
    assert stored.role is HumanRole.OBSERVER
    assert stored.created_at == _NOW


async def test_issue_rejects_naive_expires_at() -> None:
    # A naive expires_at would pass here but raise TypeError later when the
    # SSE revalidation tick compares it to the aware clock; reject it at the
    # boundary.
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    with pytest.raises(ValueError, match="timezone-aware"):
        await svc.issue(
            owner=_user(),
            name="ci",
            role=HumanRole.OBSERVER,
            expires_at=datetime(2026, 7, 1, 12, 0, 0),  # noqa: DTZ001 -- naive on purpose
        )


async def test_issue_accepts_aware_expires_at() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    expires = _NOW + timedelta(days=30)
    issued = await svc.issue(
        owner=_user(), name="ci", role=HumanRole.OBSERVER, expires_at=expires
    )
    stored = await repo.get(issued.view.id)
    assert stored is not None
    assert stored.expires_at == expires


async def test_issue_rejects_role_above_issuer() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    with pytest.raises(ForbiddenError):
        await svc.issue(
            owner=_user(role=HumanRole.OBSERVER), name="esc", role=HumanRole.CEO
        )


async def test_issue_rejects_system_role() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    with pytest.raises(ForbiddenError):
        await svc.issue(
            owner=_user(role=HumanRole.CEO), name="sys", role=HumanRole.SYSTEM
        )


async def test_issuer_can_issue_at_own_ceiling() -> None:
    # The role ceiling is inclusive: a MANAGER may mint a MANAGER-scoped
    # key (equal seniority), not only roles strictly below.
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    issued = await svc.issue(
        owner=_user(role=HumanRole.MANAGER), name="peer", role=HumanRole.MANAGER
    )
    assert issued.view.role is HumanRole.MANAGER


async def test_ceo_can_issue_any_human_role() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    issued = await svc.issue(
        owner=_user(role=HumanRole.CEO), name="mgr", role=HumanRole.MANAGER
    )
    assert issued.view.role is HumanRole.MANAGER


async def test_list_for_user_excludes_hash_and_scopes_by_user() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    await svc.issue(owner=_user(user_id="u-1"), name="a", role=HumanRole.OBSERVER)
    await svc.issue(owner=_user(user_id="u-2"), name="b", role=HumanRole.OBSERVER)

    views = await svc.list_for_user("u-1")
    assert len(views) == 1
    assert views[0].user_id == "u-1"
    # The view model has no key_hash field at all.
    assert not hasattr(views[0], "key_hash")


async def test_revoke_marks_revoked() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    issued = await svc.issue(owner=_user(), name="ci", role=HumanRole.OBSERVER)

    await svc.revoke(key_id=issued.view.id, requester=_user())

    stored = await repo.get(issued.view.id)
    assert stored is not None
    assert stored.revoked is True


async def test_revoke_is_idempotent() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    issued = await svc.issue(owner=_user(), name="ci", role=HumanRole.OBSERVER)
    await svc.revoke(key_id=issued.view.id, requester=_user())
    # Second revoke is a no-op, not an error.
    await svc.revoke(key_id=issued.view.id, requester=_user())
    stored = await repo.get(issued.view.id)
    assert stored is not None
    assert stored.revoked is True


async def test_revoke_missing_key_raises_not_found() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    with pytest.raises(ApiKeyNotFoundError):
        await svc.revoke(key_id="nope", requester=_user())


async def test_revoke_non_owner_raises_not_found() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    issued = await svc.issue(
        owner=_user(user_id="u-1"), name="ci", role=HumanRole.OBSERVER
    )
    with pytest.raises(ApiKeyNotFoundError):
        await svc.revoke(key_id=issued.view.id, requester=_user(user_id="u-2"))


async def test_ceo_can_revoke_any_key() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    issued = await svc.issue(
        owner=_user(user_id="u-1"), name="ci", role=HumanRole.OBSERVER
    )
    await svc.revoke(
        key_id=issued.view.id, requester=_user(user_id="admin", role=HumanRole.CEO)
    )
    stored = await repo.get(issued.view.id)
    assert stored is not None
    assert stored.revoked is True


async def test_issue_with_expiry_round_trips() -> None:
    repo = _FakeApiKeyRepo()
    svc = _service(repo)
    expiry = _NOW + timedelta(days=30)
    issued = await svc.issue(
        owner=_user(), name="ci", role=HumanRole.OBSERVER, expires_at=expiry
    )
    assert issued.view.expires_at == expiry
