"""Tests for `synthorg.notifications.factory`.

Focuses on per-adapter config validation in the factory's private
`_create_*_sink` helpers; `build_notification_dispatcher` is covered
transitively via the integration paths that wire it into the engine.
"""

import pytest

from synthorg.notifications.adapters.email import EmailNotificationSink
from synthorg.notifications.adapters.slack import SlackNotificationSink
from synthorg.notifications.factory import _create_email_sink, _create_slack_sink
from synthorg.settings.bridge_configs import NotificationsBridgeConfig

pytestmark = pytest.mark.unit


# Sentinel marking "delete this key" in the parametrized field tests.
_MISSING = object()


def _base_email_params() -> dict[str, str]:
    """Return a minimal valid email sink params dict.

    Kept as a factory so individual tests can mutate a copy without
    cross-test leakage.
    """
    return {
        "host": "smtp.example.test",
        "to_addrs": "alerts@example.test",
        "from_addr": "synthorg@example.test",
    }


class TestCreateEmailSink:
    """`_create_email_sink` parameter validation."""

    def test_valid_params_returns_sink(self) -> None:
        sink = _create_email_sink(_base_email_params())
        assert sink is not None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("host", _MISSING),
            ("host", ""),
            ("to_addrs", _MISSING),
            ("to_addrs", ""),
            ("from_addr", _MISSING),
            ("from_addr", ""),
            ("from_addr", "   "),
        ],
        ids=[
            "missing_host",
            "empty_host",
            "missing_to_addrs",
            "empty_to_addrs",
            "missing_from_addr",
            "empty_from_addr",
            "whitespace_from_addr",
        ],
    )
    def test_missing_or_empty_required_fields_return_none(
        self,
        field: str,
        value: object,
    ) -> None:
        """Missing or empty required fields must reject the sink.

        Includes ``from_addr``: defaulting it to ``synthorg@localhost``
        worked locally but was rejected by production SMTP relays, so
        it is now a hard requirement.
        """
        params = _base_email_params()
        if value is _MISSING:
            del params[field]
        else:
            params[field] = value  # type: ignore[assignment]
        assert _create_email_sink(params) is None

    def test_invalid_port_returns_none(self) -> None:
        params = _base_email_params()
        params["port"] = "not-a-port"
        assert _create_email_sink(params) is None

    @pytest.mark.parametrize(
        "injected",
        [
            "ops@example.test\r\nBcc: attacker@evil.test",
            "ops@example.test\nBcc: attacker@evil.test",
            "ops@example.test\rBcc: attacker@evil.test",
        ],
    )
    def test_from_addr_with_crlf_is_rejected(self, injected: str) -> None:
        """CR/LF in ``from_addr`` would let a config-edit-capable
        operator inject arbitrary extra headers (Bcc, Reply-To, ...)
        because the stdlib ``email`` package does not sanitize
        header values.
        """
        params = _base_email_params()
        params["from_addr"] = injected
        assert _create_email_sink(params) is None

    def test_from_addr_trimmed_and_accepted(self) -> None:
        """Leading / trailing whitespace around ``from_addr`` is
        trimmed and the resulting value must be non-empty.

        Also asserts the sink stores the trimmed value so an operator
        cannot sneak invisible whitespace into the ``From:`` header.
        """
        params = _base_email_params()
        params["from_addr"] = "  ops@example.test  "
        sink = _create_email_sink(params)
        assert isinstance(sink, EmailNotificationSink)
        # ``_from_addr`` is the internal attribute on ``EmailNotificationSink``;
        # this assertion catches regressions where trimming is removed.
        assert sink._from_addr == "ops@example.test"


_EXPLICIT_URL = "https://hooks.slack.com/services/X/Y/Z"
_BRIDGE_URL = "https://hooks.slack.com/services/A/B/C"


class TestCreateSlackSink:
    """`_create_slack_sink` URL resolution and bridge fallback."""

    @pytest.mark.parametrize(
        ("params_url", "bridge_url", "expected_url"),
        [
            (_EXPLICIT_URL, None, _EXPLICIT_URL),
            (_EXPLICIT_URL, _BRIDGE_URL, _EXPLICIT_URL),
            ("", _BRIDGE_URL, _BRIDGE_URL),
        ],
        ids=[
            "explicit_used_no_bridge",
            "explicit_overrides_bridge",
            "bridge_fallback",
        ],
    )
    def test_resolves_to_expected_url(
        self,
        params_url: str,
        bridge_url: str | None,
        expected_url: str,
    ) -> None:
        params: dict[str, str] = {"webhook_url": params_url} if params_url else {}
        bridge = (
            NotificationsBridgeConfig(slack_default_webhook_url=bridge_url)
            if bridge_url is not None
            else None
        )
        sink = _create_slack_sink(params, bridge_config=bridge)
        assert isinstance(sink, SlackNotificationSink)
        assert sink._webhook_url == expected_url

    @pytest.mark.parametrize(
        "bridge_url",
        [None, ""],
        ids=["no_bridge", "blank_bridge_default"],
    )
    def test_missing_url_returns_none(self, bridge_url: str | None) -> None:
        bridge = (
            NotificationsBridgeConfig(slack_default_webhook_url=bridge_url)
            if bridge_url is not None
            else None
        )
        assert _create_slack_sink({}, bridge_config=bridge) is None

    def test_invalid_explicit_url_returns_none_even_with_bridge_default(
        self,
    ) -> None:
        # An explicit per-sink ``webhook_url`` overrides the bridge
        # default, so the factory must still reject an outbound target
        # that ``SlackNotificationSink._validate_outbound_url`` blocks
        # (here a loopback IP). The bridge default is canonical and
        # would have been valid -- proving the factory does not silently
        # swap to it when the explicit URL is bad.
        bridge = NotificationsBridgeConfig(
            slack_default_webhook_url="https://hooks.slack.com/services/T/B/X",
        )
        sink = _create_slack_sink(
            {"webhook_url": "https://127.0.0.1/services/T/B/X"},
            bridge_config=bridge,
        )
        assert sink is None
