"""HTTP tests for the API-key management controller.

``POST/GET/DELETE /auth/api-keys`` issue, list, and revoke API keys.
The plaintext key is returned exactly once at issuance; the role ceiling
is enforced at the boundary; revoke is owner-or-CEO with a 404 (never
403) for non-owners.
"""

import pytest

from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

_BASE = "/api/v1/auth/api-keys"


async def test_issue_returns_plaintext_once(
    async_test_client: LoopAsyncClient,
) -> None:
    response = await async_test_client.post(
        _BASE, json={"name": "ci-key", "role": "observer"}
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["api_key"]
    assert data["key"]["name"] == "ci-key"
    assert data["key"]["role"] == "observer"
    assert data["key"]["revoked"] is False
    # The hash is never exposed on the wire.
    assert "key_hash" not in data["key"]


async def test_issued_key_appears_in_list(
    async_test_client: LoopAsyncClient,
) -> None:
    issue = await async_test_client.post(
        _BASE, json={"name": "list-me", "role": "observer"}
    )
    key_id = issue.json()["data"]["key"]["id"]
    listing = await async_test_client.get(_BASE)
    assert listing.status_code == 200
    ids = [k["id"] for k in listing.json()["data"]]
    assert key_id in ids
    assert all("key_hash" not in k for k in listing.json()["data"])


async def test_revoke_then_listed_as_revoked(
    async_test_client: LoopAsyncClient,
) -> None:
    issue = await async_test_client.post(
        _BASE, json={"name": "revoke-me", "role": "observer"}
    )
    key_id = issue.json()["data"]["key"]["id"]
    revoke = await async_test_client.delete(f"{_BASE}/{key_id}")
    assert revoke.status_code == 204
    listing = await async_test_client.get(_BASE)
    revoked = next(k for k in listing.json()["data"] if k["id"] == key_id)
    assert revoked["revoked"] is True


async def test_revoke_missing_key_returns_404(
    async_test_client: LoopAsyncClient,
) -> None:
    response = await async_test_client.delete(f"{_BASE}/does-not-exist")
    assert response.status_code == 404


async def test_role_ceiling_rejected(
    async_test_client: LoopAsyncClient,
) -> None:
    # An observer cannot mint a CEO-scoped key (403); the default client
    # header is overridden with an observer token for this request.
    response = await async_test_client.post(
        _BASE,
        json={"name": "escalate", "role": "ceo"},
        headers=make_auth_headers("observer"),
    )
    assert response.status_code == 403


async def test_observer_can_issue_observer_key(
    async_test_client: LoopAsyncClient,
) -> None:
    response = await async_test_client.post(
        _BASE,
        json={"name": "obs", "role": "observer"},
        headers=make_auth_headers("observer"),
    )
    assert response.status_code == 201


async def test_requires_authentication(
    async_test_client: LoopAsyncClient,
) -> None:
    response = await async_test_client.get(
        _BASE, headers={"Authorization": "Bearer not.a.valid.token"}
    )
    assert response.status_code in {401, 403}
