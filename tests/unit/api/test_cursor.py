"""Unit tests for the opaque pagination cursor helper."""

import base64
import json

import pytest
import structlog.testing
from hypothesis import given
from hypothesis import strategies as st

from synthorg.api.cursor import (
    CursorSecret,
    InvalidCursorError,
    decode_cursor,
    decode_keyset_cursor,
    encode_cursor,
    encode_keyset_cursor,
)
from synthorg.api.cursor_config import CursorConfig

pytestmark = pytest.mark.unit


@pytest.fixture
def stable_secret() -> CursorSecret:
    """Build a secret with a fixed 32-byte key so tests are deterministic."""
    return CursorSecret.from_key("x" * 32)


class TestEncodeDecode:
    """Round-trip semantics."""

    def test_round_trip_zero(self, stable_secret: CursorSecret) -> None:
        token = encode_cursor(0, secret=stable_secret)
        assert decode_cursor(token, secret=stable_secret) == 0

    def test_round_trip_positive(self, stable_secret: CursorSecret) -> None:
        token = encode_cursor(4242, secret=stable_secret)
        assert decode_cursor(token, secret=stable_secret) == 4242

    def test_token_is_urlsafe_base64(self, stable_secret: CursorSecret) -> None:
        token = encode_cursor(100, secret=stable_secret)
        # urlsafe_b64decode accepts the token back
        padded = token + "=" * (-len(token) % 4)
        base64.urlsafe_b64decode(padded.encode("ascii"))

    def test_stable_secret_produces_stable_token(
        self,
        stable_secret: CursorSecret,
    ) -> None:
        assert encode_cursor(7, secret=stable_secret) == encode_cursor(
            7,
            secret=stable_secret,
        )

    def test_different_secrets_produce_different_tokens(self) -> None:
        a = CursorSecret.from_key("secret-alpha-unit-test-key-pad0000")
        b = CursorSecret.from_key("secret-bravo-unit-test-key-pad0000")
        assert encode_cursor(1, secret=a) != encode_cursor(1, secret=b)


class TestTamperDetection:
    """HMAC must reject any tampered field."""

    def test_tampered_offset_rejected(self, stable_secret: CursorSecret) -> None:
        good = encode_cursor(50, secret=stable_secret)
        # Decode the outer payload, flip the offset, re-encode without re-signing.
        padded = good + "=" * (-len(good) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        payload["o"] = 999
        tampered_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        tampered = base64.urlsafe_b64encode(tampered_bytes).rstrip(b"=").decode("ascii")
        with pytest.raises(InvalidCursorError):
            decode_cursor(tampered, secret=stable_secret)

    def test_foreign_signature_rejected(self) -> None:
        signed_a = encode_cursor(
            10,
            secret=CursorSecret.from_key("secret-alpha-unit-test-key-pad0000"),
        )
        with pytest.raises(InvalidCursorError):
            decode_cursor(
                signed_a,
                secret=CursorSecret.from_key("secret-bravo-unit-test-key-pad0000"),
            )

    def test_malformed_base64_rejected(self, stable_secret: CursorSecret) -> None:
        with pytest.raises(InvalidCursorError):
            decode_cursor("not!!base64!!", secret=stable_secret)

    def test_tampered_signature_rejected(self, stable_secret: CursorSecret) -> None:
        """Direct signature-field tampering is rejected even with a valid offset."""
        good = encode_cursor(50, secret=stable_secret)
        padded = good + "=" * (-len(good) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        payload["s"] = "deadbeef" * 8  # valid-looking hex, wrong HMAC
        tampered_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        tampered = base64.urlsafe_b64encode(tampered_bytes).rstrip(b"=").decode("ascii")
        with pytest.raises(InvalidCursorError):
            decode_cursor(tampered, secret=stable_secret)

    def test_non_ascii_token_rejected(self, stable_secret: CursorSecret) -> None:
        """Tokens with non-ASCII characters are rejected as invalid base64."""
        with pytest.raises(InvalidCursorError):
            decode_cursor("cursoré", secret=stable_secret)

    def test_empty_token_rejected(self, stable_secret: CursorSecret) -> None:
        """Empty tokens are rejected at the boundary (no decode needed)."""
        with pytest.raises(InvalidCursorError):
            decode_cursor("", secret=stable_secret)

    def test_non_json_payload_rejected(self, stable_secret: CursorSecret) -> None:
        # Valid base64 but not JSON.
        token = base64.urlsafe_b64encode(b"hello world").rstrip(b"=").decode("ascii")
        with pytest.raises(InvalidCursorError):
            decode_cursor(token, secret=stable_secret)

    def test_missing_fields_rejected(self, stable_secret: CursorSecret) -> None:
        token_bytes = json.dumps({"o": 10}, separators=(",", ":")).encode("utf-8")
        token = base64.urlsafe_b64encode(token_bytes).rstrip(b"=").decode("ascii")
        with pytest.raises(InvalidCursorError):
            decode_cursor(token, secret=stable_secret)

    def test_negative_offset_rejected(self, stable_secret: CursorSecret) -> None:
        # Server must never produce one, but the decoder is the last line.
        payload = {"o": -1, "s": "deadbeef"}
        token_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        token = base64.urlsafe_b64encode(token_bytes).rstrip(b"=").decode("ascii")
        with pytest.raises(InvalidCursorError):
            decode_cursor(token, secret=stable_secret)


class TestSecretValidation:
    """CursorSecret enforces a minimum key length."""

    def test_short_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 16 bytes"):
            CursorSecret.from_key("short")

    def test_exactly_minimum_key_accepted(self) -> None:
        # 16-byte key is the minimum; must not raise.
        CursorSecret.from_key("x" * 16)


class TestEncodeValidation:
    """encode_cursor rejects invalid offsets at the source."""

    def test_negative_offset_rejected(self) -> None:
        secret = CursorSecret.from_key("encode-validation-key-32-bytes00")
        with pytest.raises(ValueError, match="must be >= 0"):
            encode_cursor(-1, secret=secret)


class TestEphemeralSecret:
    """When no key is configured, a random per-process key is used."""

    def test_ephemeral_secret_round_trips(self) -> None:
        secret = CursorSecret.ephemeral()
        token = encode_cursor(123, secret=secret)
        assert decode_cursor(token, secret=secret) == 123

    def test_ephemeral_is_ephemeral(self) -> None:
        a = CursorSecret.ephemeral()
        b = CursorSecret.ephemeral()
        # Two ephemeral secrets are different -- tokens cross-decode fail.
        token = encode_cursor(1, secret=a)
        with pytest.raises(InvalidCursorError):
            decode_cursor(token, secret=b)


class TestFromConfig:
    """Building a secret from CursorConfig."""

    def test_explicit_secret_is_stable(self) -> None:
        config = CursorConfig(secret="explicit-key-32-bytes-padding0000")
        s1 = CursorSecret.from_config(config)
        s2 = CursorSecret.from_config(config)
        token = encode_cursor(5, secret=s1)
        assert decode_cursor(token, secret=s2) == 5

    def test_none_secret_is_ephemeral(self) -> None:
        """Explicit ``None`` routes to the ephemeral branch."""
        config = CursorConfig(secret=None)
        s = CursorSecret.from_config(config)
        assert s.is_ephemeral

    def test_empty_string_secret_rejected_at_config(self) -> None:
        """``CursorConfig`` rejects ``""`` at the field validator.

        The boundary is the config model: blank strings would silently
        route to ephemeral from ``CursorSecret.from_config`` if they
        reached it, so the validator short-circuits before construction.
        """
        with pytest.raises(ValueError, match="must not be blank"):
            CursorConfig(secret="")

    def test_whitespace_only_secret_rejected_at_config(self) -> None:
        """Whitespace-only strings are treated the same as ``""``."""
        with pytest.raises(ValueError, match="must not be blank"):
            CursorConfig(secret="   \t  ")

    def test_from_env_rejects_whitespace_only_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A whitespace-only env var is a configuration mistake, not ephemeral.

        Stripping + ``or None`` would silently route a typo (``" "``,
        ``"\t"``) to the ephemeral branch, burning pagination tokens on
        every restart with no operator-visible warning. ``from_env``
        rejects explicitly so the startup failure surfaces the typo.
        """
        monkeypatch.setenv("SYNTHORG_PAGINATION_CURSOR_SECRET", "   \t ")
        with pytest.raises(ValueError, match="whitespace"):
            CursorConfig.from_env()

    def test_from_env_unset_returns_ephemeral(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unset env var builds an ephemeral config, not an error."""
        monkeypatch.delenv("SYNTHORG_PAGINATION_CURSOR_SECRET", raising=False)
        config = CursorConfig.from_env()
        assert config.secret is None


class TestObservability:
    """Decode failures emit structured logs so operators can see them."""

    def _tamper_signature(self, token: str) -> str:
        """Replace the signature with a syntactically valid wrong HMAC."""
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        payload["s"] = "deadbeef" * 8
        tampered_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(tampered_bytes).rstrip(b"=").decode("ascii")

    def test_offset_signature_tampering_logs_security_event(
        self,
        stable_secret: CursorSecret,
    ) -> None:
        """Offset HMAC mismatch fires security.cursor.signature_invalid."""
        tampered = self._tamper_signature(encode_cursor(7, secret=stable_secret))
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(InvalidCursorError),
        ):
            decode_cursor(tampered, secret=stable_secret)
        matched = [
            e for e in events if e.get("event") == "security.cursor.signature_invalid"
        ]
        assert matched, f"expected signature_invalid log; got {events}"
        assert matched[0].get("cursor_type") == "offset"
        assert matched[0].get("log_level") == "warning"

    def test_keyset_signature_tampering_logs_security_event(self) -> None:
        """Keyset HMAC mismatch fires security.cursor.signature_invalid."""
        secret = CursorSecret.from_key("keyset-observability-test-key-pad")
        tampered = self._tamper_signature(
            encode_keyset_cursor("after-key", secret=secret),
        )
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(InvalidCursorError),
        ):
            decode_keyset_cursor(tampered, secret=secret)
        matched = [
            e for e in events if e.get("event") == "security.cursor.signature_invalid"
        ]
        assert matched, f"expected signature_invalid log; got {events}"
        assert matched[0].get("cursor_type") == "keyset"

    @pytest.mark.parametrize(
        ("token", "expected_reason"),
        [
            ("", "empty_token"),
            ("x" * 513, "length_exceeded"),
            ("not!!base64!!", "base64_decode_failed"),
        ],
        ids=["empty", "too_long", "bad_base64"],
    )
    def test_decode_shape_failures_log_api_event(
        self,
        stable_secret: CursorSecret,
        token: str,
        expected_reason: str,
    ) -> None:
        """Shape failures fire api.cursor.decode_failed with a reason."""
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(InvalidCursorError),
        ):
            decode_cursor(token, secret=stable_secret)
        matched = [e for e in events if e.get("event") == "api.cursor.decode_failed"]
        assert matched, f"expected decode_failed log; got {events}"
        assert matched[0].get("reason") == expected_reason

    def test_missing_signature_field_logs_decode_failed(
        self,
        stable_secret: CursorSecret,
    ) -> None:
        """Missing 's' field fires api.cursor.decode_failed."""
        token_bytes = json.dumps({"o": 10}, separators=(",", ":")).encode("utf-8")
        token = base64.urlsafe_b64encode(token_bytes).rstrip(b"=").decode("ascii")
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(InvalidCursorError),
        ):
            decode_cursor(token, secret=stable_secret)
        matched = [e for e in events if e.get("event") == "api.cursor.decode_failed"]
        assert matched, f"expected decode_failed log; got {events}"
        assert matched[0].get("reason") == "missing_signature_field"


@given(offset=st.integers(min_value=0, max_value=10**9))
def test_round_trip_property(offset: int) -> None:
    secret = CursorSecret.from_key("hypothesis-key-32-bytes-pad000000")
    assert decode_cursor(encode_cursor(offset, secret=secret), secret=secret) == offset
