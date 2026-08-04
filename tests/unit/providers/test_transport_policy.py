"""Tests for whether a configured endpoint may be addressed in the clear.

The rule decides where a credential and a request body are allowed to
travel, so its edges (an IP literal that looks remote, a hostname that
looks local, a URL that does not parse) are the whole of its value.
"""

import pytest

from synthorg.providers.errors import ProviderValidationError
from synthorg.providers.transport_policy import (
    is_confidential_transport,
    require_confidential_transport,
    require_credentialed_endpoint,
)

pytestmark = pytest.mark.unit


class TestConfidentialTransport:
    def test_an_unconfigured_endpoint_is_confidential(self) -> None:
        # The driver addresses the provider's own published API, so there is
        # no operator-supplied target to judge.
        assert is_confidential_transport(None) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://models.invalid",
            "https://models.invalid:8443/v1",
            "HTTPS://models.invalid",
        ],
    )
    def test_tls_is_confidential_wherever_it_points(self, url: str) -> None:
        assert is_confidential_transport(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434",
            "http://LocalHost:11434",
            "http://localhost.:11434",
            "http://127.0.0.1:11434",
            "http://[::1]:11434",
            "http://[::ffff:127.0.0.1]:11434",
            "http://10.1.2.3:8000",
            "http://172.17.0.1:11434",
            "http://192.168.1.50:8000",
            "http://169.254.10.10:8000",
            "http://host.docker.internal:1234/v1",
        ],
    )
    def test_cleartext_stays_confidential_on_the_local_network(self, url: str) -> None:
        # Self-hosted inference is what base_url exists for, and every
        # shipped preset addresses one of these.
        assert is_confidential_transport(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://models.invalid:11434",
            "http://8.8.8.8:11434",
            "http://[2001:4860:4860::8888]:11434",
            # A private-network host named by DNS cannot be placed without a
            # resolution, and a resolution is not a trust decision.
            "http://vllm.lan:8000",
        ],
    )
    def test_cleartext_is_not_confidential_off_it(self, url: str) -> None:
        assert is_confidential_transport(url) is False

    @pytest.mark.parametrize(
        "url",
        [
            "ftp://models.invalid",
            "file:///etc/passwd",
            "localhost:11434",
            # An unterminated IPv6 authority, which does not parse at all.
            "http://[::1",
            "",
        ],
    )
    def test_anything_unclassifiable_is_refused(self, url: str) -> None:
        # A target this rule cannot reason about is not one it may vouch
        # for; the scheme-less form is included because it reaches here only
        # from a URL that skipped the upstream http(s) validation.
        assert is_confidential_transport(url) is False


class TestRequireConfidentialTransport:
    def test_a_confidential_target_passes(self) -> None:
        require_confidential_transport("https://models.invalid", field="Probe")

    def test_a_cleartext_remote_target_is_refused(self) -> None:
        with pytest.raises(ProviderValidationError, match="Probe"):
            require_confidential_transport("http://models.invalid:11434", field="Probe")


class TestRequireCredentialedEndpoint:
    def test_a_configured_endpoint_passes(self) -> None:
        require_credentialed_endpoint("http://localhost:11434", field="Probe")

    def test_no_endpoint_at_all_is_refused(self) -> None:
        # The driver would then route the credential from its own defaults,
        # which is neither the operator's choice nor necessarily their host.
        with pytest.raises(ProviderValidationError, match="Probe"):
            require_credentialed_endpoint(None, field="Probe")
