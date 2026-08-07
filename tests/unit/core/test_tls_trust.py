"""Tests for the process-wide outbound TLS trust snapshot.

The two transports that dial out (git subprocesses and httpx clients) read
the same configuration through different renderings, so what matters here
is that one setting produces a consistent answer on both sides: an
operator who trusts their internal CA should not find the forge API
trusting it while the clone against the same host does not.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from synthorg.core.tls_trust import (
    GIT_CA_BUNDLE_KEY,
    GIT_VERIFY_KEY,
    TlsTrust,
    current_tls_trust,
    git_tls_config,
    httpx_verify,
    set_tls_trust,
)

pytestmark = pytest.mark.unit

_BUNDLE = "/etc/ssl/corp-root.pem"


@pytest.fixture(autouse=True)
def _restore_trust() -> Iterator[None]:
    """Leave the process-wide snapshot exactly as this module found it."""
    previous = current_tls_trust()
    yield
    set_tls_trust(previous)


class TestDefaults:
    def test_an_unconfigured_process_adds_nothing(self) -> None:
        """A deployment that configured nothing must behave as before.

        Emitting keys here would put ``http.sslCAInfo=''`` on every git
        command and replace the system trust store with nothing.
        """
        set_tls_trust(TlsTrust())

        assert dict(git_tls_config()) == {}
        assert httpx_verify() is True


class TestAdditionalCa:
    def test_both_transports_receive_the_bundle(self) -> None:
        set_tls_trust(TlsTrust(ca_bundle=_BUNDLE))

        assert dict(git_tls_config()) == {GIT_CA_BUNDLE_KEY: _BUNDLE}
        assert httpx_verify() == _BUNDLE

    def test_a_bundle_does_not_disable_verification(self) -> None:
        """Naming a CA is additive; it is not a way to stop verifying."""
        set_tls_trust(TlsTrust(ca_bundle=_BUNDLE))

        assert GIT_VERIFY_KEY not in git_tls_config()
        assert httpx_verify() is not False


class TestVerifyOff:
    def test_both_transports_stop_verifying(self) -> None:
        set_tls_trust(TlsTrust(verify=False))

        assert dict(git_tls_config()) == {GIT_VERIFY_KEY: "false"}
        assert httpx_verify() is False

    def test_verify_off_wins_over_a_configured_bundle(self) -> None:
        """The two disagree only if one is read without the other.

        An operator who set a bundle and then turned verification off has
        asked for no verification; handing httpx the bundle path would
        quietly keep verifying against it.
        """
        set_tls_trust(TlsTrust(ca_bundle=_BUNDLE, verify=False))

        assert httpx_verify() is False


class TestSnapshot:
    def test_the_installed_value_is_what_later_calls_read(self) -> None:
        set_tls_trust(TlsTrust(ca_bundle=_BUNDLE, verify=False))

        trust = current_tls_trust()

        assert trust.ca_bundle == _BUNDLE
        assert trust.verify is False

    def test_the_model_is_frozen(self, tmp_path: Path) -> None:
        """A shared snapshot that callers can mutate is not a snapshot."""
        trust = TlsTrust(ca_bundle=str(tmp_path / "ca.pem"))

        with pytest.raises(ValueError, match="frozen"):
            trust.ca_bundle = str(tmp_path / "other.pem")  # type: ignore[misc]  # frozen-model probe
