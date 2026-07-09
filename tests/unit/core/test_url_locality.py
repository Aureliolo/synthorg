"""Tests for local-vs-remote provider URL classification."""

import pytest

from synthorg.core.url_locality import is_local_url


@pytest.mark.unit
class TestIsLocalUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "http://host.docker.internal:11434",
            "http://192.168.1.50:1234/v1",
            "http://10.0.0.5:8000/v1",
            "http://172.16.4.2:11434",
            "http://169.254.1.1:11434",
            "http://[::1]:11434",
            "http://[fe80::1]:11434",
            "http://[fc00::1]:11434",
            "http://[fd12::3456]:11434",
            # Schemeless host:port (a base_url may omit the scheme).
            "localhost:11434",
            "192.168.1.5:1234",
        ],
    )
    def test_local_hosts(self, url: str) -> None:
        assert is_local_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.example-provider.com/v1",
            "https://ollama.com/v1",
            "https://api.mammouth.ai/v1",
            "http://8.8.8.8/v1",
            "http://172.32.0.1/v1",
            # Ordinary hostnames beginning with fc/fd must not read as local.
            "https://fcm.example.com",
        ],
    )
    def test_remote_hosts(self, url: str) -> None:
        assert is_local_url(url) is False

    @pytest.mark.parametrize("url", [None, "", "not a url", "http://"])
    def test_absent_or_unparseable(self, url: str | None) -> None:
        assert is_local_url(url) is False
