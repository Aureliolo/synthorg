"""Tests for the secret-log redaction helpers.

These helpers protect the structured log pipeline from leaking
credential material embedded inside exception ``str(exc)`` output.
"""

import json
from typing import Any, override

import httpx
import pytest
from cryptography.fernet import Fernet, InvalidToken
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from synthorg.observability.redaction import (
    _GATE_MARKERS,
    _RULES,
    MAX_SCRUBBED_LENGTH,
    describe_without_input,
    log_exception_redacted,
    safe_error_description,
    scrub_secret_tokens,
)
from tests._shared import JsonDict


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

    @pytest.mark.parametrize(
        "key",
        [
            "x-api-key",
            "X-Api-Key",
            "x_api_key",
            "apikey",
            "x-auth-token",
            "app_client_secret",
            "db-password",
        ],
    )
    def test_strips_prefixed_and_hyphenated_json_keys(self, key: str) -> None:
        """A vendor names its credential header, not our key list.

        The keyed-colon rule skips a quoted value on purpose, so a JSON rule
        that only knows whole names lets ``{"x-api-key":"..."}`` reach the log
        with the credential intact.
        """
        body = json.dumps({key: "leaked-value-xyz", "keep": "me"})

        scrubbed = scrub_secret_tokens(body)

        assert "leaked-value-xyz" not in scrubbed
        assert json.loads(scrubbed) == {key: "***", "keep": "me"}

    def test_leaves_a_non_credential_key_alone(self) -> None:
        body = json.dumps({"encoded": "abc", "client_id": "public-app-1"})

        assert scrub_secret_tokens(body) == body


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
class TestScrubSecretTokensUnframed:
    """A credential a provider quotes back at you carries no keyword frame.

    Every other rule anchors on one (``key=``, ``"key":``,
    ``Authorization:``, ``bearer ``). A rejection body supplies none: it puts
    the key inside a sentence, and the whole body is what reaches the log.
    """

    @pytest.mark.parametrize(
        ("prefix", "body"),
        [
            ("sk-", "proj-AbCdEf1234567890XyZw"),
            ("ghp_", "AAAABBBBCCCCDDDDEEEE"),
            ("github_pat_", "11ABCDEFG0abcdefghijkl"),
            ("glpat-", "AbCdEf1234567890XyZw"),
            ("xoxb-", "1234567890-ABCDEFGHIJKL"),
            ("AIza", "SyA0123456789abcdefghijklmnopqrstuv"),
            ("AKIA", "IOSFODNN7EXAMPLE0"),
        ],
    )
    def test_an_issued_prefix_is_masked_inside_prose(
        self,
        prefix: str,
        body: str,
    ) -> None:
        raw = f"Incorrect API key provided: {prefix}{body}. Check your config."

        scrubbed = scrub_secret_tokens(raw)

        assert body not in scrubbed
        # The class survives, so the log still says which credential failed.
        assert f"{prefix}***" in scrubbed

    def test_a_named_header_echoed_back_is_masked(self) -> None:
        raw = "Invalid x-api-key: 1234567890abcdef"

        assert scrub_secret_tokens(raw) == "Invalid x-api-key: ***"

    def test_prose_about_credentials_is_left_readable(self) -> None:
        """Masking every word after "token" would cost more than it protects."""
        raw = "no secrets here, just a sentence about a token expiring"

        assert scrub_secret_tokens(raw) == raw

    def test_a_json_pair_is_not_rewritten_into_invalid_json(self) -> None:
        """The keyed-colon rule must leave quoted values to the JSON rule."""
        scrubbed = scrub_secret_tokens('{"access_token":"supersecretvalue"}')

        assert json.loads(scrubbed) == {"access_token": "***"}

    @pytest.mark.parametrize(
        "raw",
        [
            "Incorrect API key provided: sk-proj-AbCdEf1234567890XyZw",
            "Invalid x-api-key: 1234567890abcdef",
            "rejected ghp_AAAABBBBCCCCDDDDEEEE",
        ],
    )
    def test_scrubbing_twice_changes_nothing(self, raw: str) -> None:
        once = scrub_secret_tokens(raw)

        assert scrub_secret_tokens(once) == once


def _markers_in(text: str) -> set[str]:
    lowered = text.lower()
    return {marker for marker in _GATE_MARKERS if marker in lowered}


@pytest.mark.unit
class TestScrubGateContract:
    """The marker gate decides which rules run, so it must never under-admit.

    ``scrub_secret_tokens`` scans once for the union of every rule's declared
    triggers and skips the rules whose own triggers are absent. That is sound
    on exactly two conditions, and a rule silently stops running if either
    lapses: the triggers must cover everything the rule can match, and no
    replacement may introduce a trigger the subject did not already carry.
    """

    def test_every_rule_declares_lowercase_triggers(self) -> None:
        for rule in _RULES:
            assert rule.markers, f"{rule.pattern.pattern} declares no trigger"
            for marker in rule.markers:
                assert marker == marker.lower()
                assert marker in _GATE_MARKERS

    @pytest.mark.parametrize(
        "raw",
        [
            "client_secret=abc123&grant_type=client_credentials",
            '{"x-api-key":"abc1234567890"}',
            "connection refused: postgres://user:hunter2@host/db",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.body.sig",
            "auth failed: bearer eyJhbGciOiJIUzI1NiJ9",
            "Invalid x-api-key: 1234567890abcdef",
            "Incorrect API key provided: sk-proj-AbCdEf1234567890XyZw",
        ],
    )
    def test_a_scrub_introduces_no_new_trigger(self, raw: str) -> None:
        """A replacement that added a trigger would invalidate the one scan."""
        scrubbed = scrub_secret_tokens(raw)

        assert scrubbed != raw
        assert _markers_in(scrubbed) <= _markers_in(raw)

    def test_a_fernet_scrub_introduces_no_new_trigger(self) -> None:
        token = Fernet(Fernet.generate_key()).encrypt(b"payload").decode("ascii")
        raw = f"database row corrupted: {token}"

        scrubbed = scrub_secret_tokens(raw)

        assert scrubbed != raw
        assert _markers_in(scrubbed) <= _markers_in(raw)

    def test_a_subject_with_no_trigger_is_returned_unchanged(self) -> None:
        raw = "engine stage completed in 1234ms across 3 subtasks"

        assert not _markers_in(raw)
        assert scrub_secret_tokens(raw) is raw


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
            @override
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


class _CapturingLogger:
    """Minimal ``_ErrorLogger``-shaped double for ``log_exception_redacted``.

    Records the single ``error()`` call so tests can assert the event
    name, the ``error_type`` / ``error`` redaction pair, and any
    extra structured kwargs the caller passed through. Structural
    typing via the Protocol means we do not need to subclass anything.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, JsonDict]] = []

    def error(self, event: str | None = None, *args: Any, **kwargs: Any) -> None:  # type: ignore[explicit-any]  # mirrors structlog BoundLogger.error Any-typed Protocol
        # ``*args`` is part of the structural surface but unused by
        # ``log_exception_redacted``; record kwargs only. The Any-typed
        # ``**kwargs`` mirrors the production ``_ErrorLogger`` Protocol
        # (structlog's ``BoundLogger.error`` is Any-typed); narrowing
        # to ``object`` would force every ``"x" in kwargs["error"]``
        # assertion below to cast first even though the runtime values
        # the redaction helper writes are always strings.
        del args
        assert isinstance(event, str)
        self.calls.append((event, dict(kwargs)))


@pytest.mark.unit
class TestLogExceptionRedacted:
    """Direct unit tests for the ``log_exception_redacted`` helper."""

    def test_emits_error_with_redaction_pair(self) -> None:
        """Happy path: ``error()`` is called with event + redaction pair."""
        logger = _CapturingLogger()
        exc = ValueError("client_secret=cs-leak-123 in message")

        log_exception_redacted(logger, "TEST_EVENT", exc)

        assert len(logger.calls) == 1
        event, kwargs = logger.calls[0]
        assert event == "TEST_EVENT"
        assert kwargs["error_type"] == "ValueError"
        # The helper routes through ``safe_error_description`` so the
        # credential substring must NOT survive into the log record.
        assert "cs-leak-123" not in kwargs["error"]
        assert "client_secret" not in kwargs["error"] or "***" in kwargs["error"]

    def test_passes_through_extra_kwargs(self) -> None:
        """Caller-supplied structured kwargs are forwarded verbatim."""
        logger = _CapturingLogger()
        exc = RuntimeError("boom")

        log_exception_redacted(
            logger,
            "TEST_EVENT",
            exc,
            agent_id="a-1",
            task_id=42,
        )

        _, kwargs = logger.calls[0]
        assert kwargs["agent_id"] == "a-1"
        assert kwargs["task_id"] == 42
        # Redaction pair still present alongside the extras.
        assert kwargs["error_type"] == "RuntimeError"
        # ``safe_error_description`` prepends the exception class name,
        # so the rendered string is ``"<ClassName>: <msg>"``.
        assert kwargs["error"] == "RuntimeError: boom"

    def test_rejects_caller_error_type_kwarg(self) -> None:
        """``error_type=`` in kwargs raises TypeError -- helper owns this field."""
        logger = _CapturingLogger()
        exc = ValueError("v")

        with pytest.raises(TypeError, match="error_type"):
            log_exception_redacted(logger, "E", exc, error_type="OverrideAttempt")
        assert logger.calls == [], "no log emitted when the call is rejected"

    def test_rejects_caller_error_kwarg(self) -> None:
        """``error=`` in kwargs raises TypeError -- helper owns this field."""
        logger = _CapturingLogger()
        exc = ValueError("v")

        with pytest.raises(TypeError, match="error_type"):
            log_exception_redacted(logger, "E", exc, error="manual override")
        assert logger.calls == []

    @pytest.mark.parametrize("exc_info_value", [True, False, None, 1, "x"])
    def test_rejects_caller_exc_info_kwarg(
        self,
        exc_info_value: object,
    ) -> None:
        """``exc_info=`` in kwargs raises TypeError, regardless of truthiness.

        Even ``exc_info=False`` is rejected: the helper deliberately does
        not pass ``exc_info`` to ``logger.error``, so accepting a False
        value would mislead callers into thinking the kwarg is supported
        and break the guarantee the moment the value flips to truthy.
        Both `True` and `False` must raise; `None` and odd types too.
        """
        logger = _CapturingLogger()
        exc = ValueError("v")

        with pytest.raises(TypeError, match="exc_info"):
            log_exception_redacted(logger, "E", exc, exc_info=exc_info_value)
        assert logger.calls == [], "no log emitted when the call is rejected"

    def test_event_and_exc_are_positional_only(self) -> None:
        """Signature pins the first three params as positional-only."""
        logger = _CapturingLogger()
        exc = ValueError("v")

        with pytest.raises(TypeError):
            # ``event`` is positional-only; passing as keyword must
            # raise TypeError at call time so callers cannot shadow
            # the param with an extra structured field of the same name.
            log_exception_redacted(logger, event="E", exc=exc)  # type: ignore[call-arg]

    def test_chained_exception_uses_outer_type(self) -> None:
        """``raise X from Y`` sees ``type(exc).__name__`` as the OUTER class."""
        logger = _CapturingLogger()
        # Build the chained exception out-of-line so ruff TRY301 /
        # EM101 don't flag a string-literal raise nested in a try
        # block. Functional shape is identical to the natural
        # ``raise A from B`` form used in production callers.
        inner_exc = ValueError("inner-msg")
        outer_exc = RuntimeError("outer-msg")
        outer_exc.__cause__ = inner_exc

        log_exception_redacted(logger, "CHAINED", outer_exc)

        _, kwargs = logger.calls[0]
        assert kwargs["error_type"] == "RuntimeError"
        # ``safe_error_description`` only stringifies ``exc`` (not
        # ``exc.__cause__``), so the inner message must not appear.
        assert "inner-msg" not in kwargs["error"]

    def test_redacts_credential_in_exception_message(self) -> None:
        """End-to-end: a credential embedded in ``str(exc)`` is scrubbed."""
        logger = _CapturingLogger()
        # An ``httpx``-style error message that embeds a secret in a
        # URL is the canonical leak shape this helper exists to
        # prevent: ``str(exc)`` carries the URL verbatim, including
        # the credential substring, into any downstream log sink that
        # serialises the kwargs.
        exc = ValueError(
            "POST /token failed: client_secret=cs-supersecret-789 in body",
        )

        log_exception_redacted(logger, "OAUTH_FAILED", exc)

        _, kwargs = logger.calls[0]
        assert "cs-supersecret-789" not in kwargs["error"]
        assert kwargs["error_type"] == "ValueError"


class _Credentialed(BaseModel):
    """A model shaped like the credential-bearing configs in this codebase."""

    model_config = ConfigDict(extra="forbid")

    name: str
    secret: str = Field(default="", repr=False)
    port: int = 0


@pytest.mark.unit
class TestDescribeWithoutInput:
    """A validation failure over a credential-bearing model.

    ``safe_error_description`` cannot serve this: pydantic quotes the
    input it rejected, and truncates the middle of a long value, which
    removes the framing the scrubber matches on. The value has to be
    absent, not scrubbed.
    """

    @pytest.mark.parametrize(
        "secret",
        [
            pytest.param("sk-issued-token-abc123456789", id="vendor-prefixed"),
            # No prefix a scrubber could key on. This product privileges no
            # vendor, so this is the ordinary case, not the exotic one.
            pytest.param("9f2c1a8b7d6e5f4a3b2c1d0e9f8a", id="unrecognisable"),
        ],
    )
    def test_the_rejected_value_is_absent_not_scrubbed(self, secret: str) -> None:
        with pytest.raises(ValidationError) as caught:
            _Credentialed.model_validate({"secret": secret, "port": "not-a-number"})

        description = describe_without_input(caught.value)

        assert secret not in description
        assert "input_value" not in description

    def test_it_still_says_which_field_and_why(self) -> None:
        """Redaction that removes the diagnosis is not worth having."""
        with pytest.raises(ValidationError) as caught:
            _Credentialed.model_validate({"secret": "s", "port": "not-a-number"})

        description = describe_without_input(caught.value)

        assert "port" in description
        assert "name" in description

    def test_a_model_level_failure_is_labelled_rather_than_left_blank(self) -> None:
        class _Whole(BaseModel):
            a: int = 0

            @model_validator(mode="after")
            def _refuse(self) -> _Whole:
                msg = "the whole thing is wrong"
                raise ValueError(msg)

        with pytest.raises(ValidationError) as caught:
            _Whole.model_validate({})

        description = describe_without_input(caught.value)

        assert "<root>" in description
        assert "value_error" in description

    def test_a_validator_cannot_leak_its_input_through_the_message(self) -> None:
        """The one hole excluding ``input`` and ``ctx`` does not close.

        Pydantic renders ``msg`` when the exception is raised, so a
        validator that interpolates the value it rejected has already put
        it in the string and no later exclusion removes it. Such a
        message is reported by its type instead.
        """
        secret = "9f2c1a8b7d6e5f4a3b2c1d0e9f8a"

        class _Leaky(BaseModel):
            token: str

            @field_validator("token")
            @classmethod
            def _refuse(cls, value: str) -> str:
                msg = f"rejected credential {value}"
                raise ValueError(msg)

        with pytest.raises(ValidationError) as caught:
            _Leaky.model_validate({"token": secret})

        description = describe_without_input(caught.value)

        assert secret not in description
        assert "token" in description
        assert "value_error" in description

    def test_a_builtin_failure_keeps_its_message(self) -> None:
        """Pydantic's own text is constraint-derived, so it is kept.

        Reporting every error by its type slug alone would cost the
        operator the diagnosis for the overwhelming majority of failures,
        which never carried input in the first place.
        """
        with pytest.raises(ValidationError) as caught:
            _Credentialed.model_validate({"secret": "s", "port": "not-a-number"})

        description = describe_without_input(caught.value)

        assert "Field required" in description
        assert "valid integer" in description

    def test_it_is_bounded(self) -> None:
        """A blob with many bad fields must not amplify the log."""

        class _Wide(BaseModel):
            model_config = ConfigDict(extra="forbid")

        with pytest.raises(ValidationError) as caught:
            _Wide.model_validate({f"field_{index}": index for index in range(500)})

        assert len(describe_without_input(caught.value)) <= MAX_SCRUBBED_LENGTH
