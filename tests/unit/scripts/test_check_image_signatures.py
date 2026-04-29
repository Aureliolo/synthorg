"""Tests for scripts/check_image_signatures.py."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _import_script() -> ModuleType:
    """Import check_image_signatures.py as a module."""
    script = (
        Path(__file__).resolve().parents[3] / "scripts" / "check_image_signatures.py"
    )
    spec = importlib.util.spec_from_file_location("check_image_signatures", script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _import_script()


@pytest.mark.unit
class TestParseImageTagGroups:
    """parse_image_tag_groups parses repeated --image-tags inputs."""

    def test_single_group_single_pair(self) -> None:
        result = gate.parse_image_tag_groups(["backend:dev"])
        assert result == [gate.ImageTag(image="backend", tag="dev")]

    def test_single_group_multiple_pairs(self) -> None:
        result = gate.parse_image_tag_groups(["backend:dev,backend:sha-abc1234"])
        assert result == [
            gate.ImageTag(image="backend", tag="dev"),
            gate.ImageTag(image="backend", tag="sha-abc1234"),
        ]

    def test_repeated_groups(self) -> None:
        result = gate.parse_image_tag_groups(["backend:dev", "sandbox:dev"])
        assert result == [
            gate.ImageTag(image="backend", tag="dev"),
            gate.ImageTag(image="sandbox", tag="dev"),
        ]

    def test_strips_whitespace(self) -> None:
        result = gate.parse_image_tag_groups([" backend : dev , sandbox : dev "])
        assert result == [
            gate.ImageTag(image="backend", tag="dev"),
            gate.ImageTag(image="sandbox", tag="dev"),
        ]

    def test_skips_empty_entries(self) -> None:
        result = gate.parse_image_tag_groups(["backend:dev,,sandbox:dev"])
        assert result == [
            gate.ImageTag(image="backend", tag="dev"),
            gate.ImageTag(image="sandbox", tag="dev"),
        ]

    def test_dev_tag_with_dot_in_version(self) -> None:
        # Real-world dev tag form
        result = gate.parse_image_tag_groups(["sandbox:0.7.6-dev.9"])
        assert result == [gate.ImageTag(image="sandbox", tag="0.7.6-dev.9")]

    @pytest.mark.parametrize(
        "bad",
        [
            "backend",
            "backend:",
            ":dev",
            "  :  ",
        ],
    )
    def test_invalid_pair_exits_with_usage_code(self, bad: str) -> None:
        with pytest.raises(SystemExit) as exc:
            gate.parse_image_tag_groups([bad])
        assert exc.value.code == gate.USAGE_EXIT_CODE


@pytest.mark.unit
class TestImageTagStr:
    """ImageTag stringifies as 'image:tag'."""

    def test_str(self) -> None:
        assert str(gate.ImageTag(image="backend", tag="dev")) == "backend:dev"


@pytest.mark.unit
class TestPerImageConvergence:
    """_check_per_image_convergence flags divergent digests order-independently."""

    def test_empty_input(self) -> None:
        assert gate._check_per_image_convergence({}) == []

    def test_single_image_single_tag(self) -> None:
        pairs = {gate.ImageTag(image="backend", tag="dev"): "sha256:aaa"}
        assert gate._check_per_image_convergence(pairs) == []

    def test_single_image_multiple_tags_same_digest(self) -> None:
        pairs = {
            gate.ImageTag(image="backend", tag="dev"): "sha256:aaa",
            gate.ImageTag(image="backend", tag="0.7.6-dev.9"): "sha256:aaa",
            gate.ImageTag(image="backend", tag="sha-abc1234"): "sha256:aaa",
        }
        assert gate._check_per_image_convergence(pairs) == []

    def test_two_tags_diverge_reports_both_digests(self) -> None:
        pairs = {
            gate.ImageTag(image="sandbox", tag="dev"): "sha256:aaa",
            gate.ImageTag(image="sandbox", tag="0.7.6-dev.9"): "sha256:bbb",
        }
        failures = gate._check_per_image_convergence(pairs)
        assert len(failures) == 1
        msg = failures[0]
        assert "sandbox" in msg
        assert "sha256:aaa" in msg
        assert "sha256:bbb" in msg
        assert "dev" in msg
        assert "0.7.6-dev.9" in msg

    def test_three_tags_two_digests_groups_correctly(self) -> None:
        # Two tags converge, one diverges; the message should group
        # the two together and isolate the divergent one.
        pairs = {
            gate.ImageTag(image="sandbox", tag="dev"): "sha256:aaa",
            gate.ImageTag(image="sandbox", tag="0.7.6-dev.9"): "sha256:aaa",
            gate.ImageTag(image="sandbox", tag="sha-abc1234"): "sha256:bbb",
        }
        failures = gate._check_per_image_convergence(pairs)
        assert len(failures) == 1
        msg = failures[0]
        assert "sha256:aaa" in msg
        assert "sha256:bbb" in msg
        # The two-tag group must list both tags
        assert "dev" in msg
        assert "0.7.6-dev.9" in msg

    def test_divergence_is_order_independent(self) -> None:
        # Construct two dicts with the same content but different
        # iteration order. Both must produce the same finding count
        # (1 in either case) and reference both digests.
        first = {
            gate.ImageTag(image="backend", tag="a"): "sha256:111",
            gate.ImageTag(image="backend", tag="b"): "sha256:222",
        }
        second = {
            gate.ImageTag(image="backend", tag="b"): "sha256:222",
            gate.ImageTag(image="backend", tag="a"): "sha256:111",
        }
        f1 = gate._check_per_image_convergence(first)
        f2 = gate._check_per_image_convergence(second)
        assert len(f1) == 1
        assert len(f2) == 1
        # Both messages must mention both digests; the rendering may
        # vary but content equality holds.
        for msg in (f1[0], f2[0]):
            assert "sha256:111" in msg
            assert "sha256:222" in msg

    def test_multiple_images_with_independent_divergence(self) -> None:
        pairs = {
            gate.ImageTag(image="backend", tag="dev"): "sha256:aaa",
            gate.ImageTag(image="backend", tag="sha-1"): "sha256:bbb",
            gate.ImageTag(image="sandbox", tag="dev"): "sha256:ccc",
            gate.ImageTag(image="sandbox", tag="sha-1"): "sha256:ddd",
        }
        failures = gate._check_per_image_convergence(pairs)
        assert len(failures) == 2
        joined = "\n".join(failures)
        assert "backend" in joined
        assert "sandbox" in joined


@pytest.mark.unit
class TestBasicAuth:
    """_basic_auth produces a stable base64 encoding without trailing newlines."""

    def test_basic_auth_encoding(self) -> None:
        # base64("x-access-token:abc") == "eC1hY2Nlc3MtdG9rZW46YWJj"
        assert gate._basic_auth("x-access-token", "abc") == "eC1hY2Nlc3MtdG9rZW46YWJj"

    def test_no_trailing_newline(self) -> None:
        result = gate._basic_auth("user", "very-long-password-that-might-be-padded")
        assert "\n" not in result
        assert "\r" not in result


@pytest.mark.unit
class TestSignaturePresent:
    """signature_present rejects malformed digests up front."""

    def test_rejects_non_sha256_digest(self) -> None:
        # No HTTP call should be made; non-sha256 digest fails immediately.
        repo = "aureliolo/synthorg-backend"
        assert gate.signature_present(repo, "md5:deadbeef", {}) is False

    def test_rejects_empty_digest(self) -> None:
        repo = "aureliolo/synthorg-backend"
        assert gate.signature_present(repo, "", {}) is False
