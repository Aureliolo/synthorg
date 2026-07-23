"""Unit tests for the OCI reference grammar and digest verification.

A reference interpolated into a request path is the seam where the egress pin
stops being structural, so each predicate is strict; ``digest_matches`` is the
integrity check a workspace push and a digest promote both rely on, and it must
honour the digest's declared algorithm (sha256 or sha512), never assume one.
"""

import hashlib

import pytest

from synthorg.integrations.registry_api._refs import (
    digest_matches,
    valid_digest,
    valid_reference,
    valid_repository,
    valid_tag,
)

pytestmark = pytest.mark.unit

_CONTENT = b"blob-bytes"
_SHA256 = "sha256:" + hashlib.sha256(_CONTENT).hexdigest()
_SHA512 = "sha512:" + hashlib.sha512(_CONTENT).hexdigest()


class TestValidDigest:
    @pytest.mark.parametrize("value", [_SHA256, _SHA512], ids=["sha256", "sha512"])
    def test_accepts_registered_algorithms(self, value: str) -> None:
        assert valid_digest(value)

    @pytest.mark.parametrize(
        "value",
        [
            "sha256:" + "a" * 63,
            "sha256:" + "a" * 65,
            "sha512:" + "a" * 64,
            "sha256:" + "A" * 64,
            "sha1:" + "a" * 40,
            "sha256:",
            "latest",
            "",
        ],
        ids=[
            "short",
            "long",
            "512-wrong-len",
            "uppercase",
            "sha1",
            "empty-hex",
            "tag",
            "blank",
        ],
    )
    def test_rejects_malformed(self, value: str) -> None:
        assert not valid_digest(value)


class TestDigestMatches:
    def test_sha256_content_matches(self) -> None:
        assert digest_matches(_SHA256, _CONTENT)

    def test_sha512_content_matches(self) -> None:
        assert digest_matches(_SHA512, _CONTENT)

    def test_content_mismatch_is_false(self) -> None:
        assert not digest_matches(_SHA256, b"other-bytes")

    def test_sha512_is_not_verified_with_sha256(self) -> None:
        # A sha512 digest hex can never equal a sha256 hexdigest of the same
        # bytes: the algorithm must be taken from the digest, not assumed.
        wrong = "sha512:" + hashlib.sha256(_CONTENT).hexdigest()
        assert not digest_matches(wrong, _CONTENT)

    def test_unsupported_algorithm_is_false(self) -> None:
        assert not digest_matches("md5:abcdef", _CONTENT)

    def test_malformed_digest_is_false(self) -> None:
        assert not digest_matches("no-colon-here", _CONTENT)


class TestValidTag:
    @pytest.mark.parametrize("value", ["latest", "v1.2.3", "a", "_x", "A-B_c.1"])
    def test_accepts_valid_tags(self, value: str) -> None:
        assert valid_tag(value)

    @pytest.mark.parametrize(
        "value",
        ["", ".start", "-start", "a" * 129, "has space", "a/b"],
        ids=["empty", "leading-dot", "leading-dash", "too-long", "space", "slash"],
    )
    def test_rejects_invalid_tags(self, value: str) -> None:
        assert not valid_tag(value)


class TestValidRepository:
    @pytest.mark.parametrize("value", ["library/nginx", "org/team/app", "app", "a.b_c"])
    def test_accepts_valid_repositories(self, value: str) -> None:
        assert valid_repository(value)

    @pytest.mark.parametrize(
        "value",
        ["", "/leading", "trailing/", "double//slash", "UPPER", "a" * 256],
        ids=[
            "empty",
            "leading-slash",
            "trailing-slash",
            "double-slash",
            "uppercase",
            "too-long",
        ],
    )
    def test_rejects_invalid_repositories(self, value: str) -> None:
        assert not valid_repository(value)


class TestValidReference:
    def test_accepts_a_tag(self) -> None:
        assert valid_reference("latest")

    def test_accepts_a_digest(self) -> None:
        assert valid_reference(_SHA256)

    def test_rejects_garbage(self) -> None:
        assert not valid_reference("has space")
