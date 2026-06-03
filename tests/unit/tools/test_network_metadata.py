"""Unit tests for cloud-metadata / link-local endpoint detection."""

import pytest

from synthorg.tools.network_metadata import is_cloud_metadata_host

pytestmark = pytest.mark.unit


class TestIsCloudMetadataHost:
    """``is_cloud_metadata_host`` flags link-local + metadata endpoints only."""

    @pytest.mark.parametrize(
        "host",
        [
            "169.254.169.254",  # AWS / GCP / Azure IMDS
            "169.254.0.1",  # anywhere in the link-local /16
            "metadata.google.internal",
            "METADATA.GOOGLE.INTERNAL",  # case-insensitive hostname match
            "fe80::1",  # IPv6 link-local
            "::ffff:169.254.169.254",  # IPv4-mapped IPv6 metadata IP
        ],
    )
    def test_blocks_metadata_and_link_local(self, host: str) -> None:
        assert is_cloud_metadata_host(host) is True

    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",  # loopback is a legitimate app-under-test target
            "localhost",  # non-IP, non-metadata hostname
            "10.0.0.5",  # private but not link-local
            "192.168.1.10",  # private but not link-local
            "172.17.0.2",  # docker-network address
            "example.com",  # public hostname
            "8.8.8.8",  # public IP
            "",  # empty string is not an IP or known host
        ],
    )
    def test_allows_loopback_private_and_public(self, host: str) -> None:
        assert is_cloud_metadata_host(host) is False
