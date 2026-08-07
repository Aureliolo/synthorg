"""Tests for the process-wide outbound TLS trust snapshot.

The two transports that dial out (git subprocesses and httpx clients) read
the same configuration through different renderings, so what matters here
is that one setting produces a consistent answer on both sides: an
operator who trusts their internal CA should not find the forge API
trusting it while the clone against the same host does not.
"""

import ssl
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
    trust_revision,
)

pytestmark = pytest.mark.unit

_BUNDLE = "/etc/ssl/corp-root.pem"


def _write_ca(directory: Path) -> Path:
    """Write a self-signed CA the SSL context can actually load.

    Returns:
        Path to the PEM bundle.
    """
    pytest.importorskip("cryptography")
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthorg-test-ca")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    bundle = directory / "ca.pem"
    bundle.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return bundle


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
        assert isinstance(httpx_verify(), ssl.SSLContext)


class TestAdditionalCa:
    def test_both_transports_receive_the_bundle(self, tmp_path: Path) -> None:
        bundle = _write_ca(tmp_path)
        set_tls_trust(TlsTrust(ca_bundle=str(bundle)))

        assert dict(git_tls_config()) == {GIT_CA_BUNDLE_KEY: str(bundle)}
        context = httpx_verify()
        assert isinstance(context, ssl.SSLContext)
        subjects = {
            cert["subject"] for cert in context.get_ca_certs() if "subject" in cert
        }
        assert any("synthorg-test-ca" in str(subject) for subject in subjects)

    def test_the_bundle_is_added_to_the_system_roots_not_swapped_in(
        self, tmp_path: Path
    ) -> None:
        """Additive is the documented policy, and httpx's string form is not.

        Passing ``verify="<path>"`` replaces the trust store with that one
        file, so an operator naming their internal CA would silently stop
        trusting every public root.
        """
        baseline = len(ssl.create_default_context().get_ca_certs())
        set_tls_trust(TlsTrust(ca_bundle=str(_write_ca(tmp_path))))

        context = httpx_verify()

        assert isinstance(context, ssl.SSLContext)
        assert len(context.get_ca_certs()) == baseline + 1

    def test_a_bundle_does_not_disable_verification(self, tmp_path: Path) -> None:
        """Naming a CA is additive; it is not a way to stop verifying."""
        set_tls_trust(TlsTrust(ca_bundle=str(_write_ca(tmp_path))))

        assert GIT_VERIFY_KEY not in git_tls_config()
        assert httpx_verify() is not False


class TestRevision:
    def test_every_install_advances_the_revision(self) -> None:
        """A cached client compares this to know its TLS config is stale."""
        before = trust_revision()

        set_tls_trust(TlsTrust(verify=False))

        assert trust_revision() != before


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
