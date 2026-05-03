"""Boundary tests for the typed JWT contract.

Phase 2 of RFC #1711. ``decode_token`` returns a typed
:class:`synthorg.api.auth.claims.JwtClaims` and routes through
:func:`synthorg.api.boundary.parse_typed`, so a malformed claim set
emits ``api.boundary.validation_failed`` and re-raises
``ValidationError`` for the auth middleware to translate into the
standard 401 path.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
import pytest
import structlog
from pydantic import ValidationError

from synthorg.api.auth.claims import JwtClaims
from synthorg.api.auth.config import AuthConfig
from synthorg.api.auth.models import User
from synthorg.api.auth.service import AuthService
from synthorg.api.auth.system_user import (
    SYSTEM_AUDIENCE,
    SYSTEM_ISSUER,
    SYSTEM_USER_ID,
    SYSTEM_USERNAME,
    USER_AUDIENCE,
    USER_ISSUER,
)
from synthorg.api.guards import HumanRole

_SECRET = "test-secret-that-is-at-least-32-chars-long!"
_ALG: Literal["HS256"] = "HS256"


def _make_service() -> AuthService:
    return AuthService(AuthConfig(jwt_secret=_SECRET, jwt_algorithm=_ALG))


def _make_user() -> User:
    now = datetime.now(UTC)
    return User(
        id="user-001",
        username="admin",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$abc$xyz",
        role=HumanRole.CEO,
        must_change_password=False,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.unit
class TestJwtClaimsModel:
    """Direct coverage of the :class:`JwtClaims` Pydantic contract."""

    def test_user_token_round_trip(self) -> None:
        now = datetime.now(UTC)
        claims = JwtClaims(
            iss=USER_ISSUER,
            aud=USER_AUDIENCE,
            sub="user-1",
            jti="jti-1",
            iat=int(now.timestamp()),
            exp=int((now + timedelta(hours=1)).timestamp()),
            username="admin",
            role=HumanRole.CEO,
            must_change_password=False,
            pwd_sig="0123456789abcdef",
        )
        assert claims.role is HumanRole.CEO
        assert claims.pwd_sig == "0123456789abcdef"

    def test_system_token_omits_user_only_fields(self) -> None:
        now = datetime.now(UTC)
        claims = JwtClaims(
            iss=SYSTEM_ISSUER,
            aud=SYSTEM_AUDIENCE,
            sub=SYSTEM_USER_ID,
            jti="jti-system",
            iat=int(now.timestamp()),
            exp=int((now + timedelta(minutes=5)).timestamp()),
        )
        assert claims.username is None
        assert claims.role is None
        assert claims.must_change_password is None
        assert claims.pwd_sig is None

    def test_naive_datetime_iat_rejected(self) -> None:
        # Naive datetime through .timestamp() is interpreted in the
        # host's local timezone, so the same JwtClaims construction
        # yields different epoch values on different hosts. The
        # validator must reject naive values at the auth boundary so
        # token lifetimes stay deterministic across environments.
        naive = datetime(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001 -- naive value is the test input
        aware_later = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError):
            JwtClaims(
                iss=USER_ISSUER,
                aud=USER_AUDIENCE,
                sub="user-1",
                jti="jti-1",
                iat=naive,  # type: ignore[arg-type]
                exp=aware_later,  # type: ignore[arg-type]
            )

    def test_naive_datetime_exp_rejected(self) -> None:
        aware_now = datetime.now(UTC)
        naive_later = datetime(2099, 1, 1, 12, 0, 0)  # noqa: DTZ001 -- naive value is the test input
        with pytest.raises(ValidationError):
            JwtClaims(
                iss=USER_ISSUER,
                aud=USER_AUDIENCE,
                sub="user-1",
                jti="jti-1",
                iat=aware_now,  # type: ignore[arg-type]
                exp=naive_later,  # type: ignore[arg-type]
            )

    def test_iat_coerces_from_datetime(self) -> None:
        now = datetime.now(UTC)
        later = now + timedelta(hours=1)
        claims = JwtClaims(
            iss=USER_ISSUER,
            aud=USER_AUDIENCE,
            sub="user-1",
            jti="jti-1",
            iat=now,  # type: ignore[arg-type]
            exp=later,  # type: ignore[arg-type]
        )
        assert claims.iat == int(now.timestamp())
        assert claims.exp == int(later.timestamp())

    def test_iat_int_passthrough_unchanged(self) -> None:
        # PyJWT decode emits NumericDate values as int, so the
        # validator's identity branch must not mangle them.
        iat_value = 1714694400
        exp_value = iat_value + 3600
        claims = JwtClaims(
            iss=USER_ISSUER,
            aud=USER_AUDIENCE,
            sub="user-1",
            jti="jti-1",
            iat=iat_value,
            exp=exp_value,
        )
        assert claims.iat == iat_value
        assert claims.exp == exp_value

    def test_iat_must_be_strictly_less_than_exp(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            JwtClaims(
                iss=USER_ISSUER,
                aud=USER_AUDIENCE,
                sub="user-1",
                jti="jti-1",
                iat=int(now.timestamp()),
                exp=int(now.timestamp()),  # equal -> rejected
            )

    def test_partial_user_token_shape_rejected(self) -> None:
        # User-only fields must arrive as a unit; a partial set leaves
        # downstream code reading None on a path that assumes user-token
        # semantics, so the model rejects the construction.
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            JwtClaims(
                iss=USER_ISSUER,
                aud=USER_AUDIENCE,
                sub="user-1",
                jti="jti-1",
                iat=int(now.timestamp()),
                exp=int((now + timedelta(hours=1)).timestamp()),
                username="admin",
                role=HumanRole.CEO,
                must_change_password=False,
                # pwd_sig deliberately missing
            )

    def test_role_system_rejected(self) -> None:
        # System tokens carry no role claim. JwtClaims construction
        # must refuse role=HumanRole.SYSTEM so a token forged with the
        # SYSTEM enum value cannot ride through the user-token path.
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            JwtClaims(
                iss=USER_ISSUER,
                aud=USER_AUDIENCE,
                sub="user-1",
                jti="jti-1",
                iat=int(now.timestamp()),
                exp=int((now + timedelta(hours=1)).timestamp()),
                username="admin",
                role=HumanRole.SYSTEM,
                must_change_password=False,
                pwd_sig="0123456789abcdef",
            )

    def test_extra_claim_rejected(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            JwtClaims.model_validate(
                {
                    "iss": USER_ISSUER,
                    "aud": USER_AUDIENCE,
                    "sub": "user-1",
                    "jti": "jti-1",
                    "iat": int(now.timestamp()),
                    "exp": int((now + timedelta(hours=1)).timestamp()),
                    "nbf": int(now.timestamp()),  # extra
                },
            )

    def test_blank_required_claim_rejected(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            JwtClaims(
                iss=USER_ISSUER,
                aud=USER_AUDIENCE,
                sub="",  # NotBlankStr -> rejected
                jti="jti-1",
                iat=int(now.timestamp()),
                exp=int((now + timedelta(hours=1)).timestamp()),
            )


@pytest.mark.unit
class TestJwtDecodeBoundary:
    """End-to-end boundary coverage at the ``AuthService`` surface."""

    def test_decode_returns_typed_claims(self) -> None:
        svc = _make_service()
        token, _, session_id = svc.create_token(_make_user())
        claims = svc.decode_token(token)
        assert isinstance(claims, JwtClaims)
        assert claims.sub == "user-001"
        assert claims.jti == session_id

    def test_decode_rejects_extra_claim_and_emits_log(self) -> None:
        svc = _make_service()
        now = datetime.now(UTC)
        payload = {
            "iss": USER_ISSUER,
            "aud": USER_AUDIENCE,
            "sub": "user-1",
            "username": "admin",
            "role": "ceo",
            "must_change_password": False,
            "pwd_sig": "0123456789abcdef",
            "jti": "jti-1",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "nbf": int(now.timestamp()),  # extra
        }
        token = jwt.encode(payload, _SECRET, algorithm=_ALG)
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ValidationError),
        ):
            svc.decode_token(token)
        boundary_logs = [
            log for log in logs if log.get("event") == "api.boundary.validation_failed"
        ]
        assert len(boundary_logs) == 1
        assert boundary_logs[0]["boundary"] == "jwt"
        assert boundary_logs[0]["log_level"] == "warning"

    def test_decode_rejects_wrong_field_type_and_emits_log(self) -> None:
        svc = _make_service()
        now = datetime.now(UTC)
        payload = {
            "iss": USER_ISSUER,
            "aud": USER_AUDIENCE,
            "sub": "user-1",
            "jti": "jti-1",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "must_change_password": "definitely not a bool",
        }
        token = jwt.encode(payload, _SECRET, algorithm=_ALG)
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ValidationError),
        ):
            svc.decode_token(token)
        boundary_logs = [
            log for log in logs if log.get("event") == "api.boundary.validation_failed"
        ]
        assert len(boundary_logs) == 1
        assert boundary_logs[0]["boundary"] == "jwt"

    def test_system_token_shape_round_trips(self) -> None:
        svc = _make_service()
        now = datetime.now(UTC)
        payload = {
            "iss": SYSTEM_ISSUER,
            "aud": SYSTEM_AUDIENCE,
            "sub": SYSTEM_USER_ID,
            "jti": "system-jti",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        }
        token = jwt.encode(payload, _SECRET, algorithm=_ALG)
        claims = svc.decode_token(token)
        assert claims.sub == SYSTEM_USER_ID
        assert claims.username is None
        assert claims.role is None
        assert claims.pwd_sig is None
        assert claims.iss == SYSTEM_ISSUER

    def test_unknown_role_value_rejected(self) -> None:
        svc = _make_service()
        now = datetime.now(UTC)
        payload = {
            "iss": USER_ISSUER,
            "aud": USER_AUDIENCE,
            "sub": "user-1",
            "username": "admin",
            "role": "supreme-leader",  # not a valid HumanRole
            "must_change_password": False,
            "pwd_sig": "0123456789abcdef",
            "jti": "jti-1",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        }
        token = jwt.encode(payload, _SECRET, algorithm=_ALG)
        with pytest.raises(ValidationError):
            svc.decode_token(token)


@pytest.mark.unit
class TestJwtCreateBoundary:
    """Encode-side coverage: create_token now builds JwtClaims internally."""

    def test_create_emits_decodable_user_token(self) -> None:
        svc = _make_service()
        token, expires_in, session_id = svc.create_token(_make_user())
        assert expires_in > 0
        claims = svc.decode_token(token)
        assert claims.sub == "user-001"
        assert claims.role is HumanRole.CEO
        assert claims.username == "admin"
        assert claims.jti == session_id
        assert claims.pwd_sig is not None
        assert len(claims.pwd_sig) == 16

    def test_create_rejects_system_user(self) -> None:
        svc = _make_service()
        now = datetime.now(UTC)
        system_user = User(
            id=SYSTEM_USER_ID,
            username=SYSTEM_USERNAME,
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$abc$xyz",
            role=HumanRole.SYSTEM,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        with pytest.raises(ValueError, match="cannot mint SYSTEM-role tokens"):
            svc.create_token(system_user)
