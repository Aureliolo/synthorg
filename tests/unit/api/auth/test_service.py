"""Tests for AuthService."""

from datetime import UTC, datetime, timedelta

import pytest

from ai_company.api.auth.config import AuthConfig
from ai_company.api.auth.models import User
from ai_company.api.auth.service import AuthService
from ai_company.api.guards import HumanRole

_SECRET = "test-secret-that-is-at-least-32-characters-long"


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
        password_hash=svc.hash_password("test-password-12chars"),
        role=role,
        must_change_password=must_change_password,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.unit
class TestPasswordHashing:
    def test_hash_and_verify(self) -> None:
        svc = _make_service()
        hashed = svc.hash_password("my-secret-password")
        assert svc.verify_password("my-secret-password", hashed)

    def test_wrong_password_fails(self) -> None:
        svc = _make_service()
        hashed = svc.hash_password("correct-password")
        assert not svc.verify_password("wrong-password", hashed)

    def test_hash_is_not_plaintext(self) -> None:
        svc = _make_service()
        hashed = svc.hash_password("my-secret-password")
        assert hashed != "my-secret-password"
        assert "$argon2" in hashed

    def test_different_hashes_for_same_password(self) -> None:
        svc = _make_service()
        h1 = svc.hash_password("same-password")
        h2 = svc.hash_password("same-password")
        # Different salts produce different hashes
        assert h1 != h2

    def test_verify_password_with_corrupted_hash(self) -> None:
        svc = _make_service()
        assert not svc.verify_password("my-password", "not-a-valid-argon2-hash")

    def test_verify_password_with_empty_hash(self) -> None:
        svc = _make_service()
        assert not svc.verify_password("my-password", "")


@pytest.mark.unit
class TestJWT:
    def test_create_and_decode(self) -> None:
        svc = _make_service()
        user = _make_user()
        token, expires_in = svc.create_token(user)
        assert isinstance(token, str)
        assert expires_in == 1440 * 60

        claims = svc.decode_token(token)
        assert claims["sub"] == "user-001"
        assert claims["username"] == "admin"
        assert claims["role"] == "ceo"

    def test_expired_token_raises(self) -> None:
        import jwt

        config = AuthConfig(jwt_secret=_SECRET, jwt_expiry_minutes=1)
        svc = AuthService(config)
        user = _make_user()
        _token, _ = svc.create_token(user)

        # Manually create an expired token
        expired_payload = {
            "sub": user.id,
            "username": user.username,
            "role": user.role.value,
            "must_change_password": False,
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
        }
        expired_token = jwt.encode(expired_payload, _SECRET, algorithm="HS256")
        with pytest.raises(jwt.ExpiredSignatureError):
            svc.decode_token(expired_token)

    def test_invalid_signature_raises(self) -> None:
        import jwt

        svc = _make_service()
        user = _make_user()
        token, _ = svc.create_token(user)

        # Decode with wrong secret
        wrong_svc = AuthService(
            AuthConfig(jwt_secret="wrong-secret-that-is-at-least-32-chars!!")
        )
        with pytest.raises(jwt.InvalidSignatureError):
            wrong_svc.decode_token(token)

    def test_must_change_password_in_claims(self) -> None:
        svc = _make_service()
        user = _make_user(must_change_password=True)
        token, _ = svc.create_token(user)
        claims = svc.decode_token(token)
        assert claims["must_change_password"] is True

    def test_decode_token_missing_sub_claim(self) -> None:
        import jwt as pyjwt

        svc = _make_service()
        payload = {
            "username": "admin",
            "role": "ceo",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        token = pyjwt.encode(payload, _SECRET, algorithm="HS256")
        with pytest.raises(pyjwt.MissingRequiredClaimError):
            svc.decode_token(token)

    def test_create_token_empty_secret_raises(self) -> None:
        svc = AuthService(AuthConfig())
        user = _make_user()
        with pytest.raises(RuntimeError, match="JWT secret not configured"):
            svc.create_token(user)

    def test_decode_token_empty_secret_raises(self) -> None:
        svc = AuthService(AuthConfig())
        with pytest.raises(RuntimeError, match="JWT secret not configured"):
            svc.decode_token("any.token.here")


@pytest.mark.unit
class TestApiKeyHashing:
    def test_hash_deterministic(self) -> None:
        h1 = AuthService.hash_api_key("my-key")
        h2 = AuthService.hash_api_key("my-key")
        assert h1 == h2

    def test_different_keys_different_hashes(self) -> None:
        h1 = AuthService.hash_api_key("key-one")
        h2 = AuthService.hash_api_key("key-two")
        assert h1 != h2

    def test_generate_key_unique(self) -> None:
        k1 = AuthService.generate_api_key()
        k2 = AuthService.generate_api_key()
        assert k1 != k2

    def test_generate_key_length(self) -> None:
        key = AuthService.generate_api_key()
        assert len(key) > 30
