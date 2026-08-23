"""Unit tests for the notification factory's SSRF policy construction.

``NotificationSinkConfig.params`` is an untyped ``dict[str, str]``, so an
operator can persist a ``hostname_allowlist`` entry that names a host DNS
could never carry. The policy is built on the startup path, where a raise
takes the whole process down and leaves no running API through which to
correct the value, so an unusable allowlist has to disable its own sink
instead.
"""

import pytest

from synthorg.notifications._network_policy import build_sink_network_policy
from synthorg.notifications.factory import _create_ntfy_sink


@pytest.mark.unit
class TestBuildNetworkPolicy:
    """The allowlist reaches the policy, or the sink is refused."""

    def test_no_allowlist_yields_the_fail_closed_default(self) -> None:
        policy = build_sink_network_policy({}, sink_type="ntfy")
        assert policy is not None
        assert policy.hostname_allowlist == ()
        assert policy.block_private_ips is True

    def test_entries_are_parsed_and_canonicalised(self) -> None:
        policy = build_sink_network_policy(
            {"hostname_allowlist": "Git.INTERNAL, exämple.com"},
            sink_type="ntfy",
        )
        assert policy is not None
        assert policy.hostname_allowlist == ("git.internal", "xn--exmple-cua.com")

    def test_an_unusable_entry_disables_the_policy_rather_than_raising(self) -> None:
        """A persisted value that cannot canonicalise must not escape here."""
        policy = build_sink_network_policy(
            {"hostname_allowlist": "xn--bogus-.com"},
            sink_type="ntfy",
        )
        assert policy is None

    def test_an_underscore_host_is_still_usable(self) -> None:
        policy = build_sink_network_policy(
            {"hostname_allowlist": "ntfy_internal.corp"},
            sink_type="ntfy",
        )
        assert policy is not None
        assert policy.hostname_allowlist == ("ntfy_internal.corp",)


@pytest.mark.unit
class TestNtfySinkRefusesAnUnusableAllowlist:
    """One bad sink must not take the process with it."""

    def test_sink_is_disabled_when_the_allowlist_cannot_be_built(self) -> None:
        sink = _create_ntfy_sink(
            {
                "topic": "alerts",
                "server_url": "https://ntfy.example",
                "hostname_allowlist": "xn--bogus-.com",
            },
        )
        assert sink is None

    def test_sink_is_built_when_the_allowlist_is_usable(self) -> None:
        sink = _create_ntfy_sink(
            {
                "topic": "alerts",
                "server_url": "https://ntfy.example",
                "hostname_allowlist": "ntfy.internal",
            },
        )
        assert sink is not None
