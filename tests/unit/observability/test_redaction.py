"""Tests for the secret-log redaction helpers.

These helpers protect the structured log pipeline from leaking
credential material embedded inside exception ``str(exc)`` output.
"""

import json

import httpx
import pytest
from cryptography.fernet import Fernet, InvalidToken
from hypothesis import given, settings
from hypothesis import strategies as st

from synthorg.observability.redaction import (
    MAX_SCRUBBED_LENGTH,
    safe_error_description,
    scrub_secret_tokens,
)


@pytest.mark.unit
class TestScrubSecretTokensUrlEncoded:
    """URL-encoded form-body leak patterns."""

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("client_secret", "cs-abc-123"),
            ("client_id", "cid-xyz"),
            ("refresh_token", "rtk-deadbeef"),
            ("access_token", "atk-cafebabe"),
            ("code_verifier", "cv-random-bytes"),
            ("code", "auth-code-xxx"),
            ("api_key", "sk-live-123456"),
            ("api_secret", "as-secret-789"),
            ("bearer", "jwt.payload.sig"),
            ("authorization", "Bearer%20abc"),
            ("assertion", "jwt-saml"),
            ("id_token", "oidc-idt"),
            ("password", "hunter2"),
        ],
    )
    def test_strips_form_field(self, key: str, value: str) -> None:
        raw = f"grant_type=authorization_code&{key}={value}&redirect_uri=x"
        scrubbed = scrub_secret_tokens(raw)
        assert value not in scrubbed
        assert f"{key}=***" in scrubbed
        # Unrelated fields survive.
        assert "grant_type=authorization_code" in scrubbed
        assert "redirect_uri=x" in scrubbed

    def test_strips_multiple_fields_in_one_string(self) -> None:
        raw = "client_secret=sss&refresh_token=rrr&other=ok"
        scrubbed = scrub_secret_tokens(raw)
        assert "sss" not in scrubbed
        assert "rrr" not in scrubbed
        assert "other=ok" in scrubbed

    def test_does_not_strip_non_sensitive_keys(self) -> None:
        raw = "grant_type=client_credentials&scope=read%20write"
        assert scrub_secret_tokens(raw) == raw

    def test_boundary_after_equals(self) -> None:
        # A bare ``client_secret=`` with no value must not crash.
        raw = "client_secret="
        # No non-empty value to scrub; the pattern requires [^\s&]+ so this
        # should be unchanged.
        assert "client_secret" in scrub_secret_tokens(raw)


@pytest.mark.unit
class TestScrubSecretTokensJson:
    """JSON-body leak patterns."""

    @pytest.mark.parametrize(
        "key",
        [
            "access_token",
            "refresh_token",
            "client_secret",
            "code_verifier",
            "api_key",
            "api_secret",
            "authorization",
            "bearer",
            "id_token",
            "assertion",
            "password",
        ],
    )
    def test_strips_json_string_value(self, key: str) -> None:
        body = json.dumps({key: "leaked-value-xyz", "keep": "me"})
        scrubbed = scrub_secret_tokens(body)
        assert "leaked-value-xyz" not in scrubbed
        assert f'"{key}":"***"' in scrubbed or f'"{key}": "***"' in scrubbed
        assert '"keep":"me"' in scrubbed or '"keep": "me"' in scrubbed

    def test_strips_json_whitespace_variants(self) -> None:
        raw = '{"access_token"  :   "verylongvalue",  "other": "ok"}'
        scrubbed = scrub_secret_tokens(raw)
        assert "verylongvalue" not in scrubbed
        assert '"other": "ok"' in scrubbed


@pytest.mark.unit
class TestScrubSecretTokensAuthHeader:
    """HTTP ``Authorization:`` header leak patterns."""

    def test_scrubs_bearer_header(self) -> None:
        raw = "GET /v1/users\r\nAuthorization: Bearer eyJhbGciOi"
        scrubbed = scrub_secret_tokens(raw)
        assert "eyJhbGciOi" not in scrubbed
        assert "Authorization: Bearer ***" in scrubbed

    def test_scrubs_basic_header(self) -> None:
        raw = "Authorization: Basic dXNlcjpwYXNz"
        scrubbed = scrub_secret_tokens(raw)
        assert "dXNlcjpwYXNz" not in scrubbed
        assert "Authorization: Basic ***" in scrubbed

    def test_header_case_insensitive(self) -> None:
        raw = "authorization: bearer abcdef"
        scrubbed = scrub_secret_tokens(raw)
        assert "abcdef" not in scrubbed


@pytest.mark.unit
class TestScrubSecretTokensUriUserinfo:
    """URI ``<scheme>://<userinfo>@<host>`` credential leaks.

    Connection strings routinely surface in exception messages; the
    scrubber masks the whole userinfo segment so neither the
    ``user:password`` form nor the credential-only ``token@host`` form
    can slip through.
    """

    @pytest.mark.parametrize(
        ("raw", "sentinels"),
        [
            (
                "connection refused: postgres://user:hunter2@host/db",
                ("hunter2", "user:hunter2"),
            ),
            (
                "redis://:supersecret@host:6379/0 refused",
                ("supersecret", ":supersecret"),
            ),
            (
                "auth failed for https://ghp_deadbeef@github.com/owner/repo",
                ("ghp_deadbeef",),
            ),
            (
                "unix socket stall: redis://%2Fsecret.sock@host",
                ("%2Fsecret.sock", "secret.sock"),
            ),
            (
                "mysql+pymysql://admin:p%40ss@db.internal:3306/app",
                ("admin:p%40ss", "p%40ss"),
            ),
        ],
    )
    def test_masks_userinfo_segment(
        self,
        raw: str,
        sentinels: tuple[str, ...],
    ) -> None:
        scrubbed = scrub_secret_tokens(raw)
        for sentinel in sentinels:
            assert sentinel not in scrubbed, (raw, scrubbed)
        assert "***@" in scrubbed

    def test_preserves_scheme_and_host(self) -> None:
        # Non-secret framing (scheme + host) must survive so operators
        # can still identify which connection failed.
        raw = "connection refused: postgres://user:hunter2@db.internal:5432/app"
        scrubbed = scrub_secret_tokens(raw)
        assert "postgres://" in scrubbed
        assert "db.internal:5432/app" in scrubbed

    def test_does_not_touch_userinfoless_urls(self) -> None:
        # A URL without ``userinfo@`` must not be altered -- scheme +
        # host are routing metadata, not credentials.
        raw = "timeout contacting https://idp.example.com/oauth/token"
        assert scrub_secret_tokens(raw) == raw


@pytest.mark.unit
class TestScrubSecretTokensFernet:
    """Fernet ciphertext leaks (defence in depth for encrypted_sqlite)."""

    def test_scrubs_real_fernet_token(self) -> None:
        key = Fernet.generate_key()
        token = Fernet(key).encrypt(b"secret-payload").decode("ascii")
        # Sanity: a real Fernet token starts with ``gAAAAAB``.
        assert token.startswith("gAAAAAB")
        raw = f"database row corrupted: {token}"
        scrubbed = scrub_secret_tokens(raw)
        assert token not in scrubbed
        assert "***FERNET_CIPHERTEXT***" in scrubbed

    def test_does_not_flag_non_fernet_text(self) -> None:
        raw = "value gAAA too short to match"
        assert scrub_secret_tokens(raw) == raw


@pytest.mark.unit
class TestSafeErrorDescriptionBasics:
    """Shape of ``safe_error_description`` across exception kinds."""

    def test_value_error_preserved(self) -> None:
        exc = ValueError("nothing sensitive here")
        out = safe_error_description(exc)
        assert out == "ValueError: nothing sensitive here"

    def test_scrubs_oauth_leak_in_http_error_message(self) -> None:
        # Simulate an httpx error whose str carries the full POST body.
        request = httpx.Request(
            "POST",
            "https://idp.example.com/oauth/token",
            content=b"client_secret=LEAKED_CS&code_verifier=LEAKED_CV",
        )
        response = httpx.Response(400, request=request, text="error")
        exc = httpx.HTTPStatusError(
            (
                "Server error '400 Bad Request' for url "
                "'https://idp.example.com/oauth/token'. "
                "Body: client_secret=LEAKED_CS&code_verifier=LEAKED_CV"
            ),
            request=request,
            response=response,
        )
        out = safe_error_description(exc)
        assert out.startswith("HTTPStatusError: ")
        assert "LEAKED_CS" not in out
        assert "LEAKED_CV" not in out
        assert "client_secret=***" in out
        assert "code_verifier=***" in out
        # The useful non-secret parts survive for operator debugging.
        assert "400" in out
        # Assert on the full fixture URL rather than a bare host
        # substring so CodeQL's "incomplete URL substring sanitization"
        # heuristic stays quiet (the rule would otherwise flag
        # ``"idp.example.com" in out`` as a partial URL check, which
        # is a false positive in an assertion on our own log output).
        assert "https://idp.example.com/oauth/token" in out

    def test_scrubs_json_body_in_error_message(self) -> None:
        exc = RuntimeError(
            'provider returned: {"access_token":"atk-LEAK","refresh_token":"rtk-LEAK"}',
        )
        out = safe_error_description(exc)
        assert "atk-LEAK" not in out
        assert "rtk-LEAK" not in out

    def test_fernet_invalid_token(self) -> None:
        out = safe_error_description(InvalidToken())
        assert out.startswith("InvalidToken")

    def test_json_decode_error(self) -> None:
        try:
            json.loads("{not json}")
        except json.JSONDecodeError as exc:
            out = safe_error_description(exc)
        else:  # pragma: no cover
            pytest.fail("JSONDecodeError not raised")
        assert out.startswith("JSONDecodeError:")

    def test_non_ascii_does_not_crash(self) -> None:
        exc = ValueError("Ошибка: client_secret=ляля")
        out = safe_error_description(exc)
        assert "ляля" not in out
        assert "client_secret=***" in out

    def test_binary_bytes_repr_survives(self) -> None:
        exc = ValueError("bad value: b'\\x00\\x01\\x02'")
        out = safe_error_description(exc)
        assert out.startswith("ValueError:")

    def test_base_exception_system_exit_scrubbed(self) -> None:
        # ``SystemExit`` is a ``BaseException`` subclass, not an ``Exception``.
        # Our helper accepts ``BaseException`` -- make sure it still scrubs.
        out = safe_error_description(SystemExit("oops client_secret=LEAKED"))
        assert "LEAKED" not in out
        assert out.startswith("SystemExit:")

    def test_broken_str_method_does_not_crash(self) -> None:
        # Some exceptions have broken ``__str__`` (e.g., custom ones that
        # recurse or call a method that raises). The helper must never
        # propagate that failure -- a broken description is better than
        # a dropped log event.
        class BrokenStrError(Exception):
            def __str__(self) -> str:
                msg = "no str for you"
                raise RuntimeError(msg)

        out = safe_error_description(BrokenStrError())
        # Falls back to the repr path; at minimum, the type name is
        # always present.
        assert "BrokenStrError" in out

    def test_percent_encoded_url_form_value_scrubbed(self) -> None:
        # ``client_secret=%2A%26%2A`` contains a percent-encoded ``&``
        # in the middle. The old regex stopped at the first ``&`` and
        # only masked the prefix; the new pattern masks the whole value.
        raw = "grant_type=x&client_secret=%2A%26%2A&next=value"
        scrubbed = scrub_secret_tokens(raw)
        assert "%2A%26%2A" not in scrubbed
        assert "client_secret=***" in scrubbed
        assert "next=value" in scrubbed


@pytest.mark.unit
class TestSafeErrorDescriptionTruncation:
    """Output length is capped to prevent log amplification."""

    def test_long_message_truncated(self) -> None:
        exc = ValueError("x" * (MAX_SCRUBBED_LENGTH * 4))
        out = safe_error_description(exc)
        assert len(out) <= MAX_SCRUBBED_LENGTH
        assert out.endswith("...[truncated]")

    def test_short_message_not_truncated(self) -> None:
        exc = ValueError("short")
        assert safe_error_description(exc) == "ValueError: short"


@pytest.mark.unit
class TestScrubIdempotent:
    """Scrubbing is stable: running it twice is equivalent to once."""

    @given(
        st.text(
            alphabet=st.characters(min_codepoint=32, max_codepoint=126),
            max_size=512,
        ),
    )
    @settings(max_examples=200)
    def test_idempotent(self, text: str) -> None:
        once = scrub_secret_tokens(text)
        twice = scrub_secret_tokens(once)
        assert once == twice

    @given(
        st.text(
            alphabet=st.characters(min_codepoint=32, max_codepoint=126),
            max_size=512,
        ),
    )
    @settings(max_examples=200)
    def test_never_grows_unbounded(self, text: str) -> None:
        # Scrubbing replaces matched substrings with fixed-size placeholders,
        # so output must not exceed input length + a small constant per
        # possible replacement. 32 is a conservative upper bound.
        out = scrub_secret_tokens(text)
        assert len(out) <= len(text) + 32 * (len(text) // 16 + 1)
