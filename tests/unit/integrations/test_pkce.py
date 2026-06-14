"""Unit tests for PKCE utilities."""

import pytest

from synthorg.integrations.errors import MasterKeyError, PKCEValidationError
from synthorg.integrations.oauth.pkce import (
    OAuthPKCECipher,
    _reset_cipher_for_tests,
    generate_code_challenge,
    generate_code_verifier,
    init_pkce_cipher,
    validate_code_challenge,
    validate_code_verifier,
)

_VALID_FERNET_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="


@pytest.mark.unit
class TestCodeVerifier:
    """Tests for code verifier generation and validation."""

    def test_generate_returns_128_chars(self) -> None:
        verifier = generate_code_verifier()
        assert len(verifier) == 128

    def test_generate_uses_unreserved_chars(self) -> None:
        import re

        verifier = generate_code_verifier()
        assert re.match(r"^[A-Za-z0-9\-._~]+$", verifier)

    def test_generate_returns_unique_values(self) -> None:
        v1 = generate_code_verifier()
        v2 = generate_code_verifier()
        assert v1 != v2

    def test_validate_accepts_valid_verifier(self) -> None:
        verifier = generate_code_verifier()
        validate_code_verifier(verifier)

    @pytest.mark.parametrize("size", [42, 129])
    def test_validate_rejects_invalid_lengths(self, size: int) -> None:
        with pytest.raises(PKCEValidationError, match="43-128"):
            validate_code_verifier("a" * size)

    def test_validate_rejects_invalid_chars(self) -> None:
        with pytest.raises(PKCEValidationError, match="invalid"):
            validate_code_verifier("a" * 43 + " ")


@pytest.mark.unit
class TestCodeChallenge:
    """Tests for code challenge generation and validation."""

    def test_challenge_is_base64url(self) -> None:
        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)
        assert "+" not in challenge
        assert "/" not in challenge
        assert "=" not in challenge

    def test_challenge_is_deterministic(self) -> None:
        verifier = generate_code_verifier()
        c1 = generate_code_challenge(verifier)
        c2 = generate_code_challenge(verifier)
        assert c1 == c2

    def test_validate_challenge_succeeds(self) -> None:
        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)
        validate_code_challenge(verifier, challenge)

    def test_validate_challenge_rejects_mismatch(self) -> None:
        verifier = generate_code_verifier()
        with pytest.raises(PKCEValidationError, match="does not match"):
            validate_code_challenge(verifier, "wrong")


@pytest.mark.unit
class TestOAuthPKCECipher:
    """Tests for the eager-validating PKCE verifier cipher."""

    def test_roundtrip(self) -> None:
        cipher = OAuthPKCECipher(_VALID_FERNET_KEY)
        verifier = generate_code_verifier()
        assert cipher.decrypt(cipher.encrypt(verifier)) == verifier

    def test_blank_key_raises_at_construction(self) -> None:
        with pytest.raises(MasterKeyError, match="must be set"):
            OAuthPKCECipher("   ")

    def test_invalid_key_raises_at_construction(self) -> None:
        with pytest.raises(MasterKeyError, match="Invalid Fernet key"):
            OAuthPKCECipher("not-a-valid-fernet-key")

    def test_from_env_reads_master_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_MASTER_KEY", _VALID_FERNET_KEY)
        cipher = OAuthPKCECipher.from_env()
        verifier = generate_code_verifier()
        assert cipher.decrypt(cipher.encrypt(verifier)) == verifier


@pytest.mark.unit
class TestInitPkceCipher:
    """Tests for the best-effort boot initialiser."""

    def test_noop_when_key_absent(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SYNTHORG_MASTER_KEY", raising=False)
        _reset_cipher_for_tests()
        # Absent key must not raise at boot (OAuth stays optional).
        init_pkce_cipher()
        _reset_cipher_for_tests()

    def test_raises_when_present_key_invalid(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_MASTER_KEY", "corrupt-key")
        _reset_cipher_for_tests()
        with pytest.raises(MasterKeyError):
            init_pkce_cipher()
        _reset_cipher_for_tests()

    def test_validates_present_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_MASTER_KEY", _VALID_FERNET_KEY)
        _reset_cipher_for_tests()
        init_pkce_cipher()
        # Second call is idempotent.
        init_pkce_cipher()
        _reset_cipher_for_tests()
