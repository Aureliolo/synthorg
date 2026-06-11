# module-kind: tests
"""Tests for scripts/check_image_signatures.py."""

import importlib.util
import urllib.error
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
class TestIsImmutableTag:
    """_is_immutable_tag distinguishes build-identity tags from floating ones."""

    @pytest.mark.parametrize(
        "tag",
        [
            "sha-abc1234",
            "sha-bb915d8",
            "0.8.9",
            "0.8.9-dev.51",
            "1.0.0-rc.1",
            "1.2.3+build.4",
            "v1.0.0",  # optional v-prefix (future tag-policy hardening)
            "v0.8.9-dev.51",
        ],
    )
    def test_immutable(self, tag: str) -> None:
        assert gate._is_immutable_tag(tag) is True

    @pytest.mark.parametrize(
        "tag",
        [
            "dev",
            "latest",
            "0.8",  # floating major.minor
            "0",  # floating major
            "sha-1",  # too short to be a commit sha
            "sha-xyz1234",  # non-hex tail
        ],
    )
    def test_floating(self, tag: str) -> None:
        assert gate._is_immutable_tag(tag) is False


@pytest.mark.unit
class TestPerImageConvergence:
    """_check_per_image_convergence flags divergence across IMMUTABLE tags only."""

    def test_empty_input(self) -> None:
        assert gate._check_per_image_convergence({}) == []

    def test_single_immutable_tag(self) -> None:
        pairs = {gate.ImageTag(image="backend", tag="sha-abc1234"): "sha256:aaa"}
        assert gate._check_per_image_convergence(pairs) == []

    def test_single_image_multiple_tags_same_digest(self) -> None:
        pairs = {
            gate.ImageTag(image="backend", tag="dev"): "sha256:aaa",
            gate.ImageTag(image="backend", tag="0.7.6-dev.9"): "sha256:aaa",
            gate.ImageTag(image="backend", tag="sha-abc1234"): "sha256:aaa",
        }
        assert gate._check_per_image_convergence(pairs) == []

    def test_floating_dev_may_diverge_from_immutable_pair(self) -> None:
        # The dev.51 false positive: the floating `dev` tag advanced to a
        # newer (signed) build while the immutable version + sha tags stay
        # converged. This must NOT be reported -- floating tags are allowed
        # to point elsewhere, and per-pair signature checks still cover them.
        pairs = {
            gate.ImageTag(image="backend", tag="dev"): "sha256:newer",
            gate.ImageTag(image="backend", tag="0.8.9-dev.51"): "sha256:aaa",
            gate.ImageTag(image="backend", tag="sha-bb915d8"): "sha256:aaa",
        }
        assert gate._check_per_image_convergence(pairs) == []

    def test_immutable_tags_diverge_reports_both_digests(self) -> None:
        # The real race the gate exists to catch: the immutable version tag
        # and its sha tag (both pin THIS build) resolve to different digests.
        pairs = {
            gate.ImageTag(image="sandbox", tag="0.7.6-dev.9"): "sha256:aaa",
            gate.ImageTag(image="sandbox", tag="sha-abc1234"): "sha256:bbb",
        }
        failures = gate._check_per_image_convergence(pairs)
        assert len(failures) == 1
        msg = failures[0]
        assert "sandbox" in msg
        assert "sha256:aaa" in msg
        assert "sha256:bbb" in msg
        assert "0.7.6-dev.9" in msg
        assert "sha-abc1234" in msg

    def test_floating_tag_excluded_from_divergent_group(self) -> None:
        # Two immutable tags converge, a third immutable tag diverges; the
        # floating `dev` tag is excluded from the report even though it
        # points at one of the digests. If it were not excluded, the aaa
        # group would render `[0.7.6-dev.9, dev]` instead of `[0.7.6-dev.9]`.
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
        assert "tags [0.7.6-dev.9]" in msg
        assert "tags [sha-abc1234]" in msg

    def test_divergence_is_order_independent(self) -> None:
        # Same content, different iteration order -> identical result. Use
        # two immutable tags so the divergence is actually reported.
        first = {
            gate.ImageTag(image="backend", tag="0.7.6-dev.9"): "sha256:111",
            gate.ImageTag(image="backend", tag="sha-abc1234"): "sha256:222",
        }
        second = {
            gate.ImageTag(image="backend", tag="sha-abc1234"): "sha256:222",
            gate.ImageTag(image="backend", tag="0.7.6-dev.9"): "sha256:111",
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
            gate.ImageTag(image="backend", tag="0.7.6-dev.9"): "sha256:aaa",
            gate.ImageTag(image="backend", tag="sha-abc1234"): "sha256:bbb",
            gate.ImageTag(image="sandbox", tag="0.7.6-dev.9"): "sha256:ccc",
            gate.ImageTag(image="sandbox", tag="sha-def5678"): "sha256:ddd",
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
    """signature_present validates digests and tolerates propagation lag."""

    def test_rejects_non_sha256_digest(self) -> None:
        # No HTTP call should be made; non-sha256 digest fails immediately.
        repo = "aureliolo/synthorg-backend"
        assert gate.signature_present(repo, "md5:deadbeef", {}) is False

    def test_rejects_empty_digest(self) -> None:
        repo = "aureliolo/synthorg-backend"
        assert gate.signature_present(repo, "", {}) is False

    def test_present_on_first_check_does_not_sleep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def fake_request(
            _method: str, url: str, _headers: dict[str, str]
        ) -> tuple[int, dict[str, str], bytes]:
            calls.append(url)
            return gate.HTTP_OK, {}, b""

        slept: list[float] = []
        monkeypatch.setattr(gate, "_request", fake_request)
        monkeypatch.setattr(gate.time, "sleep", slept.append)
        digest = "sha256:" + "a" * 64
        assert gate.signature_present("aureliolo/synthorg-backend", digest, {}) is True
        assert len(calls) == 1
        assert slept == []

    def test_retries_propagation_lag_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        statuses = iter([404, gate.HTTP_OK])

        def fake_request(
            _method: str, _url: str, _headers: dict[str, str]
        ) -> tuple[int, dict[str, str], bytes]:
            return next(statuses), {}, b""

        slept: list[float] = []
        monkeypatch.setattr(gate, "_request", fake_request)
        monkeypatch.setattr(gate.time, "sleep", slept.append)
        digest = "sha256:" + "b" * 64
        assert gate.signature_present("aureliolo/synthorg-backend", digest, {}) is True
        assert slept == [gate.SIG_PROPAGATION_BACKOFF_SECONDS]

    def test_transient_network_error_is_retried_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A network blip on the first attempt must be retried, not fail the
        # whole check; a subsequent 200 resolves it.
        results: list[object] = [urllib.error.URLError("blip"), (gate.HTTP_OK, {}, b"")]

        def fake_request(
            _method: str, _url: str, _headers: dict[str, str]
        ) -> tuple[int, dict[str, str], bytes]:
            item = results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item  # type: ignore[return-value]

        slept: list[float] = []
        monkeypatch.setattr(gate, "_request", fake_request)
        monkeypatch.setattr(gate.time, "sleep", slept.append)
        digest = "sha256:" + "d" * 64
        assert gate.signature_present("aureliolo/synthorg-backend", digest, {}) is True
        assert len(slept) == 1

    def test_persistent_network_error_reraises_not_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A network error that persists past the budget must re-raise (the
        # caller reports a network error), not be masked as a False
        # "unsigned" verdict.
        def fake_request(
            _method: str, _url: str, _headers: dict[str, str]
        ) -> tuple[int, dict[str, str], bytes]:
            msg = "down"
            raise urllib.error.URLError(msg)

        slept: list[float] = []
        monkeypatch.setattr(gate, "_request", fake_request)
        monkeypatch.setattr(gate.time, "sleep", slept.append)
        digest = "sha256:" + "e" * 64
        with pytest.raises(urllib.error.URLError):
            gate.signature_present("aureliolo/synthorg-backend", digest, {})
        assert len(slept) == gate.SIG_PROPAGATION_ATTEMPTS - 1

    def test_persistent_raw_oserror_normalised_to_urlerror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A raw OSError (not already a URLError) that persists past the budget
        # is normalised to URLError so callers only need to catch URLError;
        # the original OSError is preserved as __cause__.
        original = ConnectionResetError("connection reset by peer")

        def fake_request(
            _method: str, _url: str, _headers: dict[str, str]
        ) -> tuple[int, dict[str, str], bytes]:
            raise original

        slept: list[float] = []
        monkeypatch.setattr(gate, "_request", fake_request)
        monkeypatch.setattr(gate.time, "sleep", slept.append)
        digest = "sha256:" + "a" * 64
        with pytest.raises(urllib.error.URLError) as excinfo:
            gate.signature_present("aureliolo/synthorg-backend", digest, {})
        assert excinfo.value.__cause__ is original
        assert len(slept) == gate.SIG_PROPAGATION_ATTEMPTS - 1

    def test_genuine_miss_fails_after_bounded_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def fake_request(
            _method: str, url: str, _headers: dict[str, str]
        ) -> tuple[int, dict[str, str], bytes]:
            calls.append(url)
            return 404, {}, b""

        slept: list[float] = []
        monkeypatch.setattr(gate, "_request", fake_request)
        monkeypatch.setattr(gate.time, "sleep", slept.append)
        digest = "sha256:" + "c" * 64
        assert gate.signature_present("aureliolo/synthorg-backend", digest, {}) is False
        assert len(calls) == gate.SIG_PROPAGATION_ATTEMPTS
        assert len(slept) == gate.SIG_PROPAGATION_ATTEMPTS - 1

    def test_persistent_registry_error_reraises_not_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A non-404 registry error (e.g. 502) that persists past the budget
        # is a registry failure, NOT evidence of a missing signature: it must
        # re-raise (the caller reports a network error) rather than be masked
        # as a False "unsigned" verdict. Only a 404 means "genuinely absent".
        calls: list[str] = []

        def fake_request(
            _method: str, url: str, _headers: dict[str, str]
        ) -> tuple[int, dict[str, str], bytes]:
            calls.append(url)
            return 502, {}, b""

        slept: list[float] = []
        monkeypatch.setattr(gate, "_request", fake_request)
        monkeypatch.setattr(gate.time, "sleep", slept.append)
        digest = "sha256:" + "d" * 64
        with pytest.raises(urllib.error.URLError):
            gate.signature_present("aureliolo/synthorg-backend", digest, {})
        assert len(calls) == gate.SIG_PROPAGATION_ATTEMPTS
        assert len(slept) == gate.SIG_PROPAGATION_ATTEMPTS - 1

    def test_transient_registry_error_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A non-404 registry error on an early attempt is retried within the
        # budget; a subsequent 200 still resolves to True (signature present).
        statuses = iter([502, gate.HTTP_OK])

        def fake_request(
            _method: str, _url: str, _headers: dict[str, str]
        ) -> tuple[int, dict[str, str], bytes]:
            return next(statuses), {}, b""

        slept: list[float] = []
        monkeypatch.setattr(gate, "_request", fake_request)
        monkeypatch.setattr(gate.time, "sleep", slept.append)
        digest = "sha256:" + "f" * 64
        assert gate.signature_present("aureliolo/synthorg-backend", digest, {}) is True
        assert slept == [gate.SIG_PROPAGATION_BACKOFF_SECONDS]


@pytest.mark.unit
class TestRepoPrefixValidator:
    """_validate_repo_prefix rejects values that could escape the URL path."""

    @pytest.mark.parametrize(
        "good",
        [
            "aureliolo/synthorg-",
            "library/foo-",
            "ns/sub.ns/foo-",
            "a-b/c-d-",
        ],
    )
    def test_accepts_valid(self, good: str) -> None:
        gate._validate_repo_prefix(good)  # does not raise

    @pytest.mark.parametrize(
        "bad",
        [
            "EVIL/",
            "aureliolo/synthorg",
            "..",
            "../etc-",
            "aureliolo/synth org-",
            "aureliolo/synthorg-\n",
            "/aureliolo/synthorg-",
            "",
            "aureliolo//synthorg-",
            "aureliolo/synthorg-/",
        ],
    )
    def test_rejects_invalid(self, bad: str) -> None:
        with pytest.raises(SystemExit) as exc:
            gate._validate_repo_prefix(bad)
        assert exc.value.code == gate.USAGE_EXIT_CODE


@pytest.mark.unit
class TestImageTagValidator:
    """_validate_image_tag rejects values that could escape the URL path."""

    @pytest.mark.parametrize(
        ("image", "tag"),
        [
            ("backend", "dev"),
            ("backend", "0.7.6-dev.9"),
            ("sandbox", "sha-2531b65"),
            ("fine-tune-cpu", "0.7.6"),
            ("fine.tune", "v_1"),
        ],
    )
    def test_accepts_valid(self, image: str, tag: str) -> None:
        pair = gate.ImageTag(image=image, tag=tag)
        gate._validate_image_tag(pair)  # does not raise

    @pytest.mark.parametrize(
        ("image", "tag"),
        [
            ("backend/../etc", "dev"),  # path traversal
            ("backend", "foo\nbar"),  # CRLF
            ("backend", "foo bar"),  # space
            ("backend", ".dotstart"),  # leading dot
            ("backend", "-dashstart"),  # leading dash
            ("EVIL", "dev"),  # uppercase image
            ("backend", "x" * 129),  # tag length > 128
            ("", "dev"),  # empty image
            ("backend", ""),  # empty tag
            ("backend", "foo:bar"),  # colon in tag
            ("backend", "foo/bar"),  # slash in tag
        ],
    )
    def test_rejects_invalid(self, image: str, tag: str) -> None:
        pair = gate.ImageTag(image=image, tag=tag)
        with pytest.raises(SystemExit) as exc:
            gate._validate_image_tag(pair)
        assert exc.value.code == gate.USAGE_EXIT_CODE


@pytest.mark.unit
class TestVerifyPair:
    """_verify_pair returns errors instead of raising on transient failures."""

    def test_network_error_becomes_failure_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mock resolve_digest to raise a URLError; _verify_pair should
        # catch it and return a structured failure message rather than
        # propagating.
        import urllib.error

        err = urllib.error.URLError("connection refused")

        def boom(*_args: object, **_kwargs: object) -> tuple[str | None, str | None]:
            raise err

        monkeypatch.setattr(gate, "resolve_digest", boom)
        pair = gate.ImageTag(image="backend", tag="dev")
        digest, returned_err = gate._verify_pair(pair, "aureliolo/synthorg-", {})
        assert digest is None
        assert returned_err is not None
        assert "URLError" in returned_err
        assert "connection refused" in returned_err

    def test_signature_missing_becomes_failure_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If resolve_digest succeeds but signature_present returns False,
        # _verify_pair must surface that as an error and embed the
        # expected referrer-tag for diagnosability.
        monkeypatch.setattr(
            gate, "resolve_digest", lambda *_a, **_k: ("sha256:abc123", None)
        )
        monkeypatch.setattr(gate, "signature_present", lambda *_a, **_k: False)
        pair = gate.ImageTag(image="backend", tag="dev")
        digest, err = gate._verify_pair(pair, "aureliolo/synthorg-", {})
        assert digest is None
        assert err is not None
        assert "no cosign signature artifact" in err
        assert "sha256-abc123" in err

    def test_success_returns_digest_and_no_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            gate, "resolve_digest", lambda *_a, **_k: ("sha256:deadbeef", None)
        )
        monkeypatch.setattr(gate, "signature_present", lambda *_a, **_k: True)
        pair = gate.ImageTag(image="backend", tag="dev")
        digest, err = gate._verify_pair(pair, "aureliolo/synthorg-", {})
        assert digest == "sha256:deadbeef"
        assert err is None


@pytest.mark.unit
class TestAuthHeaderForRepo:
    """_auth_header_for_repo caches the per-repo Bearer header."""

    def test_caches_header_across_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # mint_pull_token must be called only once per repo_path even
        # when the helper is invoked many times.
        call_count = 0

        def counting_mint(*_a: object, **_k: object) -> str | None:
            nonlocal call_count
            call_count += 1
            return f"minted-token-{call_count}"

        monkeypatch.setattr(gate, "mint_pull_token", counting_mint)
        cache: dict[str, dict[str, str]] = {}
        h1, e1 = gate._auth_header_for_repo("aureliolo/synthorg-backend", "tok", cache)
        h2, e2 = gate._auth_header_for_repo("aureliolo/synthorg-backend", "tok", cache)
        h3, e3 = gate._auth_header_for_repo("aureliolo/synthorg-sandbox", "tok", cache)
        assert e1 is None
        assert e2 is None
        assert e3 is None
        assert h1 == h2  # same repo: same cached header
        assert h1 != h3  # different repo: different mint
        assert call_count == 2  # one mint per unique repo

    def test_returns_empty_header_when_no_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gate, "mint_pull_token", lambda *_a, **_k: None)
        cache: dict[str, dict[str, str]] = {}
        header, err = gate._auth_header_for_repo(
            "aureliolo/synthorg-backend", None, cache
        )
        assert err is None
        assert header == {}

    def test_network_error_becomes_failure_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.error

        err = urllib.error.URLError("dns failure")

        def boom(*_a: object, **_k: object) -> str | None:
            raise err

        monkeypatch.setattr(gate, "mint_pull_token", boom)
        cache: dict[str, dict[str, str]] = {}
        header, returned_err = gate._auth_header_for_repo(
            "aureliolo/synthorg-backend", "tok", cache
        )
        assert header is None
        assert returned_err is not None
        assert "URLError" in returned_err
        assert "dns failure" in returned_err
