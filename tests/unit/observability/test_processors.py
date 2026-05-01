"""Tests for custom structlog processors."""

import pytest

from synthorg.observability.processors import (
    sanitize_sensitive_fields,
    scrub_event_fields,
)


@pytest.mark.unit
class TestSanitizeSensitiveFields:
    """Tests for the sanitize_sensitive_fields processor."""

    def test_redacts_password(self) -> None:
        event = {"event": "login", "password": "s3cret"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["password"] == "**REDACTED**"
        assert result["event"] == "login"

    def test_redacts_api_key(self) -> None:
        event = {"api_key": "abc123", "event": "request"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["api_key"] == "**REDACTED**"

    def test_redacts_api_secret(self) -> None:
        event = {"api_secret": "xyz", "event": "call"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["api_secret"] == "**REDACTED**"

    def test_redacts_token(self) -> None:
        event = {"auth_token": "jwt.stuff", "event": "auth"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["auth_token"] == "**REDACTED**"

    def test_redacts_authorization(self) -> None:
        event = {"authorization": "Bearer xyz", "event": "header"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["authorization"] == "**REDACTED**"

    def test_redacts_secret(self) -> None:
        event = {"client_secret": "shh", "event": "oauth"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["client_secret"] == "**REDACTED**"

    def test_redacts_credential(self) -> None:
        event = {"credential": "data", "event": "verify"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["credential"] == "**REDACTED**"

    def test_case_insensitive(self) -> None:
        event = {"PASSWORD": "upper", "Api_Key": "mixed"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["PASSWORD"] == "**REDACTED**"
        assert result["Api_Key"] == "**REDACTED**"

    def test_preserves_non_sensitive_fields(self) -> None:
        event = {"event": "hello", "user": "alice", "count": 42}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result == event

    def test_returns_new_dict(self) -> None:
        event = {"password": "secret", "event": "test"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result is not event
        assert event["password"] == "secret"

    def test_empty_event_dict(self) -> None:
        result = sanitize_sensitive_fields(None, "info", {})
        assert result == {}

    def test_multiple_sensitive_fields(self) -> None:
        event = {
            "password": "p",
            "api_key": "k",
            "token": "t",
            "event": "multi",
        }
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["password"] == "**REDACTED**"
        assert result["api_key"] == "**REDACTED**"
        assert result["token"] == "**REDACTED**"
        assert result["event"] == "multi"

    def test_redacts_private_key(self) -> None:
        event = {"private_key": "-----BEGIN RSA", "event": "ssh"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["private_key"] == "**REDACTED**"

    def test_redacts_bearer(self) -> None:
        event = {"bearer": "xyz", "event": "auth"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["bearer"] == "**REDACTED**"

    def test_redacts_session(self) -> None:
        event = {"session_id": "abc123", "event": "track"}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["session_id"] == "**REDACTED**"

    def test_non_string_key_preserved(self) -> None:
        event: dict[str | int, str] = {42: "value", "event": "test"}
        result = sanitize_sensitive_fields(None, "info", event)  # type: ignore[arg-type]
        assert result[42] == "value"  # type: ignore[index]
        assert result["event"] == "test"

    def test_redacts_nested_dict(self) -> None:
        event = {"event": "req", "payload": {"token": "secret", "user": "alice"}}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["payload"]["token"] == "**REDACTED**"
        assert result["payload"]["user"] == "alice"

    def test_redacts_deeply_nested(self) -> None:
        event = {"event": "req", "outer": {"inner": {"password": "deep"}}}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["outer"]["inner"]["password"] == "**REDACTED**"

    def test_redacts_in_list_of_dicts(self) -> None:
        event = {"event": "batch", "items": [{"api_key": "k1"}, {"name": "ok"}]}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["items"][0]["api_key"] == "**REDACTED**"
        assert result["items"][1]["name"] == "ok"

    def test_redacts_in_tuple_of_dicts(self) -> None:
        event = {"event": "batch", "items": ({"secret": "s"},)}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["items"][0]["secret"] == "**REDACTED**"

    def test_nested_non_sensitive_preserved(self) -> None:
        event = {"event": "req", "data": {"name": "alice", "count": 5}}
        result = sanitize_sensitive_fields(None, "info", event)
        assert result["data"] == {"name": "alice", "count": 5}


@pytest.mark.unit
class TestScrubEventFields:
    """Deep-scrub processor -- secret-leak regression guards."""

    def test_scrubs_error_str_exc_leak(self) -> None:
        # Simulates ``logger.warning(EVENT, error=str(exc))`` where the
        # exception carried the POST body.
        event = {
            "event": "oauth_exchange_failed",
            "error": (
                "Client error '400 Bad Request' for url "
                "'https://idp/token'. "
                "Body: client_secret=LEAKED&refresh_token=RTK_LEAK"
            ),
        }
        result = scrub_event_fields(None, "warning", event)
        assert "LEAKED" not in result["error"]
        assert "RTK_LEAK" not in result["error"]
        assert "client_secret=***" in result["error"]
        assert "refresh_token=***" in result["error"]
        # Non-credential context preserved.
        assert "400" in result["error"]
        assert "idp" in result["error"]
        # Unrelated key unchanged.
        assert result["event"] == "oauth_exchange_failed"

    def test_scrubs_nested_dict(self) -> None:
        event = {
            "event": "req",
            "request": {
                "body": "client_secret=LEAKED&grant_type=refresh_token",
            },
        }
        result = scrub_event_fields(None, "info", event)
        body = result["request"]["body"]
        assert "LEAKED" not in body
        assert "client_secret=***" in body

    def test_scrubs_tuple_of_strings(self) -> None:
        event = {
            "event": "batch",
            "payloads": (
                "access_token=A1_LEAK",
                "refresh_token=A2_LEAK",
            ),
        }
        result = scrub_event_fields(None, "info", event)
        assert "A1_LEAK" not in result["payloads"][0]
        assert "A2_LEAK" not in result["payloads"][1]

    def test_scrubs_list(self) -> None:
        event = {"event": "batch", "items": ["client_secret=X_LEAK", "other"]}
        result = scrub_event_fields(None, "info", event)
        assert "X_LEAK" not in result["items"][0]
        assert result["items"][1] == "other"

    def test_scrubs_fernet_ciphertext(self) -> None:
        # Realistic Fernet-prefixed string.
        leaky = "row unreadable: gAAAAABLeaKeDcipheRTeXt_xxxxxxxxxxxxxxxxxxxxxxxx"
        event = {"event": "db_error", "detail": leaky}
        result = scrub_event_fields(None, "error", event)
        assert "gAAAAAB" not in result["detail"]
        assert "***FERNET_CIPHERTEXT***" in result["detail"]

    def test_preserves_non_string_leaves(self) -> None:
        event = {
            "event": "metric",
            "count": 42,
            "ratio": 0.95,
            "flag": True,
        }
        result = scrub_event_fields(None, "info", event)
        assert result["count"] == 42
        assert result["ratio"] == 0.95
        assert result["flag"] is True

    def test_idempotent(self) -> None:
        event = {"event": "x", "msg": "client_secret=CS&other=ok"}
        once = scrub_event_fields(None, "info", event)
        twice = scrub_event_fields(None, "info", dict(once))
        assert once == twice

    def test_returns_new_dict(self) -> None:
        event = {"event": "x", "msg": "client_secret=LEAK"}
        result = scrub_event_fields(None, "info", event)
        # Original untouched (immutability convention).
        assert event["msg"] == "client_secret=LEAK"
        # Result scrubbed.
        assert result["msg"] == "client_secret=***"


@pytest.mark.unit
class TestScrubEventFieldsEndToEnd:
    """End-to-end: the processor runs inside the configured structlog chain."""

    def test_leak_scrubbed_via_real_logger(self) -> None:
        """``logger.warning(..., error=str(exc))`` leak is closed by the
        processor when the leaker goes through the configured pipeline."""
        import structlog.testing

        from synthorg.observability import get_logger

        logger = get_logger("test.sec1.scrubber")
        with structlog.testing.capture_logs() as events:
            logger.warning(
                "upstream_failed",
                error="POST body: client_secret=ENDTOEND_LEAK&code_verifier=CVLEAK",
            )
        # Note: ``capture_logs`` intercepts BEFORE the main processor chain
        # runs, so this test validates the processor wiring separately by
        # invoking it manually against the raw event dict.  The first
        # assertion is a control.
        assert any("ENDTOEND_LEAK" in str(e) for e in events), (
            "capture_logs should still show the raw event here"
        )
        # Apply the processor as configure_logging would:
        scrubbed = [scrub_event_fields(logger, "warning", dict(e)) for e in events]
        for e in scrubbed:
            assert "ENDTOEND_LEAK" not in str(e)
            assert "CVLEAK" not in str(e)
            assert "client_secret=***" in str(e)


@pytest.mark.unit
class TestScrubEventFieldsProcessorChain:
    """Guard: the processor must stay wired into the global pipeline.

    These tests catch regressions that would silently disable the
    secret-leak defence -- dropping the processor from
    ``_BASE_PROCESSORS`` or reordering it before ``format_exc_info``
    (so tracebacks bypass the scrubber).
    """

    def test_processor_is_registered_in_base_processors(self) -> None:
        from synthorg.observability.setup import _BASE_PROCESSORS

        assert scrub_event_fields in _BASE_PROCESSORS, (
            "scrub_event_fields must be part of the base processor chain; "
            "removing it would reopen the credential-leak channel."
        )

    def test_processor_runs_after_format_exc_info(self) -> None:
        # The processor must see the flattened ``exc_info`` rendering,
        # otherwise exception text bypasses the scrubber.
        import structlog

        from synthorg.observability.setup import _BASE_PROCESSORS

        format_exc_info = structlog.processors.format_exc_info
        assert format_exc_info in _BASE_PROCESSORS, (
            "format_exc_info must be part of the base processor chain; "
            "its removal would leave exception payloads unrendered."
        )
        assert _BASE_PROCESSORS.index(scrub_event_fields) > _BASE_PROCESSORS.index(
            format_exc_info,
        ), (
            "scrub_event_fields must run AFTER format_exc_info; otherwise "
            "exception text is scrubbed before it exists in the event dict."
        )

    def test_fail_open_returns_event_dict_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Force ``_scrub_value`` to raise so the fail-open branch runs.
        # Patching the internal helper is the deterministic way to
        # exercise the ``except Exception`` arm without crafting a
        # pathological input that might be silently tolerated by a
        # future scrubber refactor.
        from synthorg.observability import processors as processors_mod

        def _raise(_value: object) -> object:
            msg = "boom"
            raise RuntimeError(msg)

        monkeypatch.setattr(processors_mod, "_scrub_value", _raise)

        event = {"event": "x", "payload": "whatever"}
        # The processor must return a dict and not raise; the caller's
        # log pipeline would otherwise die on a single bad event.
        result = scrub_event_fields(None, "warning", event)
        assert result is event, (
            "fail-open branch must return the original dict to prevent "
            "partial scrubbing from dropping the event"
        )
