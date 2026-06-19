"""Unit tests for the self-hosted forge webhook verifiers."""

import hashlib
import hmac

import pytest

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.webhooks.verifiers.factory import get_verifier
from synthorg.integrations.webhooks.verifiers.forge_verifiers import (
    ForgejoHmacVerifier,
    GiteaHmacVerifier,
    GitLabTokenVerifier,
)

pytestmark = pytest.mark.unit


def _hmac(body: bytes, secret: str) -> str:
    """Return the raw-hex HMAC-SHA256 of *body* under *secret*."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def test_gitea_accepts_valid_signature() -> None:
    body = b'{"ref": "refs/heads/main"}'
    result = await GiteaHmacVerifier().verify(
        body=body,
        headers={"x-gitea-signature": _hmac(body, "sek")},
        secret="sek",
    )
    assert result is True


async def test_gitea_rejects_bad_signature() -> None:
    result = await GiteaHmacVerifier().verify(
        body=b"x",
        headers={"x-gitea-signature": "deadbeef"},
        secret="sek",
    )
    assert result is False


async def test_forgejo_accepts_primary_header() -> None:
    body = b"payload"
    result = await ForgejoHmacVerifier().verify(
        body=body,
        headers={"x-forgejo-signature": _hmac(body, "sek")},
        secret="sek",
    )
    assert result is True


async def test_forgejo_accepts_legacy_gitea_header() -> None:
    body = b"payload"
    result = await ForgejoHmacVerifier().verify(
        body=body,
        headers={"x-gitea-signature": _hmac(body, "sek")},
        secret="sek",
    )
    assert result is True


async def test_forgejo_rejects_missing_header() -> None:
    result = await ForgejoHmacVerifier().verify(
        body=b"x",
        headers={},
        secret="sek",
    )
    assert result is False


async def test_gitlab_accepts_matching_token() -> None:
    result = await GitLabTokenVerifier().verify(
        body=b"ignored",
        headers={"x-gitlab-token": "shared-secret"},
        secret="shared-secret",
    )
    assert result is True


@pytest.mark.parametrize(
    "headers",
    [{"x-gitlab-token": "wrong"}, {}],
)
async def test_gitlab_rejects_bad_or_missing_token(
    headers: dict[str, str],
) -> None:
    result = await GitLabTokenVerifier().verify(
        body=b"ignored",
        headers=headers,
        secret="shared-secret",
    )
    assert result is False


@pytest.mark.parametrize(
    ("connection_type", "expected"),
    [
        (ConnectionType.GITLAB, GitLabTokenVerifier),
        (ConnectionType.GITEA, GiteaHmacVerifier),
        (ConnectionType.FORGEJO, ForgejoHmacVerifier),
    ],
)
def test_factory_maps_forge_types(
    connection_type: ConnectionType,
    expected: type,
) -> None:
    assert isinstance(get_verifier(connection_type), expected)
