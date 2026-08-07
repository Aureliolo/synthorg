"""Tests for ``TlsTrustSettingsSubscriber``.

The subscriber is what makes ``security.tls_ca_bundle`` /
``security.tls_verify`` live rather than boot-baked, so an operator who
adds their internal CA reaches the next clone and the next forge API call
with it. Both keys are re-read together whichever one changed, because
applying half the pair would install a bundle with verification already
off, or the reverse.
"""

from collections.abc import Iterator
from unittest.mock import create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.tls_trust import TlsTrust, current_tls_trust, set_tls_trust
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.tls_trust_subscriber import (
    TlsTrustSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_BUNDLE = "/etc/ssl/corp-root.pem"


@pytest.fixture(autouse=True)
def _restore_trust() -> Iterator[None]:
    """Leave the process-wide snapshot exactly as this module found it."""
    previous = current_tls_trust()
    yield
    set_tls_trust(previous)


class _StubResolver:
    """Answers the two reads the subscriber makes."""

    def __init__(self, *, ca_bundle: str, verify: bool) -> None:
        self._ca_bundle = ca_bundle
        self._verify = verify

    async def get_str(self, namespace: str, key: str) -> str:
        """Return the configured bundle path.

        Returns:
            The bundle path.
        """
        del namespace, key
        return self._ca_bundle

    async def get_bool(self, namespace: str, key: str) -> bool:
        """Return whether verification is on.

        Returns:
            The verify flag.
        """
        del namespace, key
        return self._verify


def _make_subscriber(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ca_bundle: str = _BUNDLE,
    verify: bool = True,
) -> TlsTrustSettingsSubscriber:
    """Build a subscriber whose resolver answers with the given pair.

    Returns:
        The subscriber under test.
    """
    from synthorg.settings import state as settings_state

    app_state = make_app_state(config=RootConfig(company_name="test"))
    monkeypatch.setattr(
        settings_state,
        "config_resolver_of",
        lambda _state: _StubResolver(ca_bundle=ca_bundle, verify=verify),
    )
    return TlsTrustSettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )


class TestProtocol:
    def test_isinstance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert isinstance(_make_subscriber(monkeypatch), SettingsSubscriber)

    def test_watched_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert _make_subscriber(monkeypatch).watched_keys == frozenset(
            {("security", "tls_ca_bundle"), ("security", "tls_verify")}
        )

    def test_subscriber_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert _make_subscriber(monkeypatch).subscriber_name == "tls-trust"


class TestReapply:
    @pytest.mark.parametrize("key", ["tls_ca_bundle", "tls_verify"])
    async def test_either_key_installs_the_whole_pair(
        self, monkeypatch: pytest.MonkeyPatch, key: str
    ) -> None:
        """Half a pair is a bundle trusted with verification off, or worse."""
        set_tls_trust(TlsTrust())
        sub = _make_subscriber(monkeypatch, ca_bundle=_BUNDLE, verify=False)

        await sub.on_settings_changed([("security", key)])

        trust = current_tls_trust()
        assert trust.ca_bundle == _BUNDLE
        assert trust.verify is False

    async def test_one_apply_for_a_batch_touching_both(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_tls_trust(TlsTrust())
        sub = _make_subscriber(monkeypatch, ca_bundle=_BUNDLE, verify=True)

        await sub.on_settings_changed(
            [("security", "tls_ca_bundle"), ("security", "tls_verify")]
        )

        assert current_tls_trust().ca_bundle == _BUNDLE

    async def test_unknown_key_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        installed = TlsTrust(ca_bundle="/etc/ssl/untouched.pem")
        set_tls_trust(installed)
        sub = _make_subscriber(monkeypatch, ca_bundle=_BUNDLE)

        await sub.on_settings_changed([("security", "unrelated")])

        assert current_tls_trust().ca_bundle == installed.ca_bundle

    async def test_resolve_failure_reraises_and_leaves_trust_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A half-read pair must not be installed over a working one."""
        from synthorg.settings import state as settings_state

        installed = TlsTrust(ca_bundle="/etc/ssl/untouched.pem")
        set_tls_trust(installed)
        app_state = make_app_state(config=RootConfig(company_name="test"))

        def _boom(_state: AppState) -> object:
            msg = "resolver boom"
            raise RuntimeError(msg)

        monkeypatch.setattr(settings_state, "config_resolver_of", _boom)
        sub = TlsTrustSettingsSubscriber(
            app_state=app_state,
            settings_service=create_autospec(SettingsService, instance=True),
        )

        with pytest.raises(RuntimeError, match="resolver boom"):
            await sub.on_settings_changed([("security", "tls_verify")])

        assert current_tls_trust().ca_bundle == installed.ca_bundle
