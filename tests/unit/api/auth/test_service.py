"""Tests for AuthService."""

import asyncio
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from synthorg.api.auth.service import AuthService, SecretNotConfiguredError
from synthorg.api.auth.system_user import USER_AUDIENCE, USER_ISSUER
from synthorg.core.auth.config import AuthConfig
from synthorg.core.auth.models import User
from synthorg.core.auth.roles import HumanRole
from tests.unit.api.conftest import _TEST_JWT_SECRET as _SECRET


def _make_service() -> AuthService:
    return AuthService(AuthConfig(jwt_secret=_SECRET))


def _make_user(
    *,
    role: HumanRole = HumanRole.CEO,
    must_change_password: bool = False,
) -> User:
    now = datetime.now(UTC)
    svc = _make_service()
    return User(
        id="user-001",
        username="admin",
        password_hash=asyncio.run(svc.hash_password("test-password-12chars")),
        role=role,
        must_change_password=must_change_password,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.unit
class TestPasswordHashing:
    async def test_hash_and_verify(self) -> None:
        svc = _make_service()
        hashed = await svc.hash_password("my-secret-password")
        assert await svc.verify_password("my-secret-password", hashed)

    async def test_wrong_password_fails(self) -> None:
        svc = _make_service()
        hashed = await svc.hash_password("correct-password")
        assert not await svc.verify_password("wrong-password", hashed)

    async def test_hash_is_not_plaintext(self) -> None:
        svc = _make_service()
        hashed = await svc.hash_password("my-secret-password")
        assert hashed != "my-secret-password"
        assert "$argon2" in hashed

    async def test_different_hashes_for_same_password(self) -> None:
        svc = _make_service()
        h1 = await svc.hash_password("same-password")
        h2 = await svc.hash_password("same-password")
        # Different salts produce different hashes
        assert h1 != h2

    async def test_verify_password_with_corrupted_hash_raises(self) -> None:
        import argon2.exceptions

        svc = _make_service()
        with pytest.raises(argon2.exceptions.InvalidHashError):
            await svc.verify_password("my-password", "not-a-valid-argon2-hash")

    async def test_verify_password_with_empty_hash_raises(self) -> None:
        import argon2.exceptions

        svc = _make_service()
        with pytest.raises(argon2.exceptions.InvalidHashError):
            await svc.verify_password("my-password", "")


@pytest.mark.unit
class TestJWT:
    def test_create_and_decode(self) -> None:
        svc = _make_service()
        user = _make_user()
        token, expires_in, session_id = svc.create_token(user)
        assert isinstance(token, str)
        assert expires_in == 1440 * 60
        assert isinstance(session_id, str)
        assert len(session_id) == 32  # uuid4().hex

        claims = svc.decode_token(token)
        assert claims.sub == "user-001"
        assert claims.username == "admin"
        assert claims.role is not None
        assert claims.role.value == "ceo"
        assert claims.jti == session_id
        # User tokens carry the API issuer/audience pair so a leaked
        # CLI token cannot replay as a user token (or vice versa).
        assert claims.iss == USER_ISSUER
        assert claims.aud == USER_AUDIENCE

    def test_create_token_reuses_supplied_session_id(self) -> None:
        # Refresh rotation passes the consumed record's session id so
        # the new access token stays in the same session (same jti).
        svc = _make_service()
        user = _make_user()
        token, _, session_id = svc.create_token(user, session_id="fixed-session-xyz")
        assert session_id == "fixed-session-xyz"
        assert svc.decode_token(token).jti == "fixed-session-xyz"

    def test_expired_token_raises(self) -> None:
        config = AuthConfig(jwt_secret=_SECRET, jwt_expiry_minutes=1)
        svc = AuthService(config)
        user = _make_user()
        _token, _, _ = svc.create_token(user)

        # Manually create an expired token
        expired_payload = {
            "iss": USER_ISSUER,
            "aud": USER_AUDIENCE,
            "sub": user.id,
            "username": user.username,
            "role": user.role.value,
            "must_change_password": False,
            "jti": "expired-jti",
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
        }
        expired_token = jwt.encode(expired_payload, _SECRET, algorithm="HS256")
        with pytest.raises(jwt.ExpiredSignatureError):
            svc.decode_token(expired_token)

    def test_decode_token_missing_iss_claim(self) -> None:
        """A token without ``iss`` is rejected at decode time."""
        svc = _make_service()
        user = _make_user()
        payload = {
            # Deliberately no "iss".
            "aud": USER_AUDIENCE,
            "sub": user.id,
            "username": user.username,
            "role": user.role.value,
            "jti": "abc",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        token = jwt.encode(payload, _SECRET, algorithm="HS256")
        with pytest.raises(jwt.MissingRequiredClaimError):
            svc.decode_token(token)

    def test_decode_token_missing_aud_claim(self) -> None:
        """A token without ``aud`` is rejected at decode time."""
        svc = _make_service()
        user = _make_user()
        payload = {
            "iss": USER_ISSUER,
            # Deliberately no "aud".
            "sub": user.id,
            "username": user.username,
            "role": user.role.value,
            "jti": "abc",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        token = jwt.encode(payload, _SECRET, algorithm="HS256")
        with pytest.raises(jwt.MissingRequiredClaimError):
            svc.decode_token(token)

    def test_decode_token_does_not_verify_issuer_value(self) -> None:
        """``decode_token`` accepts any ``iss`` value (presence only).

        Issuer *value* enforcement intentionally lives in
        :func:`_resolve_jwt_user` so the middleware can pick the
        canonical pair per-role (``synthorg-cli`` for SYSTEM tokens,
        ``synthorg-api`` for human user tokens) AFTER loading the user
        record. The end-to-end rejection path for wrong-value issuers
        is exercised by
        ``test_middleware.test_user_jwt_with_wrong_issuer_returns_401``
        and ``test_middleware.test_system_user_jwt_with_wrong_issuer_returns_401``.

        This test pins the service-layer contract: a token with a
        bogus ``iss`` decodes successfully here, and the middleware is
        the layer that rejects it.
        """
        svc = _make_service()
        user = _make_user()
        payload = {
            "iss": "attacker",
            "aud": USER_AUDIENCE,
            "sub": user.id,
            "username": user.username,
            "role": user.role.value,
            "must_change_password": False,
            "pwd_sig": "0123456789abcdef",
            "jti": "abc",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        token = jwt.encode(payload, _SECRET, algorithm="HS256")
        claims = svc.decode_token(token)
        assert claims.iss == "attacker"

    def test_decode_token_does_not_verify_audience_value(self) -> None:
        """``decode_token`` accepts any ``aud`` value (presence only).

        Wrong-value audience rejection is exercised by
        ``test_middleware.test_user_jwt_with_wrong_audience_returns_401``
        and ``test_middleware.test_system_user_jwt_with_wrong_audience_returns_401``.
        """
        svc = _make_service()
        user = _make_user()
        payload = {
            "iss": USER_ISSUER,
            "aud": "attacker",
            "sub": user.id,
            "username": user.username,
            "role": user.role.value,
            "must_change_password": False,
            "pwd_sig": "0123456789abcdef",
            "jti": "abc",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        token = jwt.encode(payload, _SECRET, algorithm="HS256")
        claims = svc.decode_token(token)
        assert claims.aud == "attacker"

    def test_invalid_signature_raises(self) -> None:
        svc = _make_service()
        user = _make_user()
        token, _, _ = svc.create_token(user)

        # Decode with wrong secret
        wrong_svc = AuthService(
            AuthConfig(jwt_secret="wrong-secret-that-is-at-least-32-chars!!")
        )
        with pytest.raises(jwt.InvalidSignatureError):
            wrong_svc.decode_token(token)

    def test_must_change_password_in_claims(self) -> None:
        svc = _make_service()
        user = _make_user(must_change_password=True)
        token, _, _ = svc.create_token(user)
        claims = svc.decode_token(token)
        assert claims.must_change_password is True

    def test_decode_token_missing_sub_claim(self) -> None:
        svc = _make_service()
        payload = {
            "iss": USER_ISSUER,
            "aud": USER_AUDIENCE,
            "username": "admin",
            "role": "ceo",
            "jti": "abc",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        token = jwt.encode(payload, _SECRET, algorithm="HS256")
        with pytest.raises(jwt.MissingRequiredClaimError):
            svc.decode_token(token)

    def test_create_token_empty_secret_raises(self) -> None:
        svc = AuthService(AuthConfig())
        user = _make_user()
        with pytest.raises(SecretNotConfiguredError, match="JWT secret not configured"):
            svc.create_token(user)

    def test_decode_token_empty_secret_raises(self) -> None:
        svc = AuthService(AuthConfig())
        with pytest.raises(SecretNotConfiguredError, match="JWT secret not configured"):
            svc.decode_token("any.token.here")


@pytest.mark.unit
class TestApiKeyHashing:
    def test_hash_deterministic(self) -> None:
        svc = _make_service()
        h1 = svc.hash_api_key("my-key")
        h2 = svc.hash_api_key("my-key")
        assert h1 == h2

    def test_different_keys_different_hashes(self) -> None:
        svc = _make_service()
        h1 = svc.hash_api_key("key-one")
        h2 = svc.hash_api_key("key-two")
        assert h1 != h2

    def test_hash_requires_secret(self) -> None:
        svc = AuthService(AuthConfig())
        with pytest.raises(SecretNotConfiguredError, match="JWT secret not configured"):
            svc.hash_api_key("some-key")

    def test_different_secrets_produce_different_hashes(self) -> None:
        svc_a = AuthService(
            AuthConfig(jwt_secret="secret-a-that-is-at-least-32-characters!")
        )
        svc_b = AuthService(
            AuthConfig(jwt_secret="secret-b-that-is-at-least-32-characters!")
        )
        h_a = svc_a.hash_api_key("same-key")
        h_b = svc_b.hash_api_key("same-key")
        assert h_a != h_b

    def test_hash_output_is_64_char_hex(self) -> None:
        svc = _make_service()
        result = svc.hash_api_key("test-key")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_generate_key_unique(self) -> None:
        k1 = AuthService.generate_api_key()
        k2 = AuthService.generate_api_key()
        assert k1 != k2

    def test_generate_key_length(self) -> None:
        key = AuthService.generate_api_key()
        assert len(key) > 30
