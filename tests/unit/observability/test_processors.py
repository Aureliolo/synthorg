"""Tests for custom structlog processors."""

import pytest

from synthorg.observability.processors import sanitize_sensitive_fields

pytestmark = pytest.mark.timeout(30)


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
