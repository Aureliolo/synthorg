"""Unit tests for ``scripts/protect_ghcr_undeletable_version.py``.

A ``keep-*`` tag exempts its target from every future prune, and nothing
revisits it, so the safety guard is the whole tool: pointing it at a real
multi-arch release image would pin that image outside cleanup permanently.
That guard is a pure function, so it is tested directly rather than through
the network path.
"""

import importlib.util
import urllib.error
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "protect_ghcr_undeletable_version.py"

_OCI_INDEX = "application/vnd.oci.image.index.v1+json"
_OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def _load_script_module() -> ModuleType:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_protect_ghcr_undeletable_version",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


# ── _assert_safe_to_protect ─────────────────────────────────────


def test_refuses_a_platform_bearing_index() -> None:
    """The case that matters: a real multi-arch release image."""
    manifest = {
        "manifests": [
            {
                "digest": "sha256:a",
                "platform": {"os": "linux", "architecture": "amd64"},
            },
            {
                "digest": "sha256:b",
                "platform": {"os": "linux", "architecture": "arm64"},
            },
        ]
    }
    with pytest.raises(_MODULE.GhcrProtectionError):
        _MODULE._assert_safe_to_protect(manifest, _OCI_INDEX)


def test_refuses_an_index_with_a_single_platform_child() -> None:
    """One platform child is still a real image, not an orphan."""
    manifest = {"manifests": [{"platform": {"os": "linux", "architecture": "amd64"}}]}
    with pytest.raises(_MODULE.GhcrProtectionError):
        _MODULE._assert_safe_to_protect(manifest, _OCI_INDEX)


def test_allows_an_attestation_index_with_no_platform_children() -> None:
    """The orphan case this tool exists for: an index of sigstore bundles."""
    manifest = {"manifests": [{"digest": "sha256:a"}, {"digest": "sha256:b"}]}
    _MODULE._assert_safe_to_protect(manifest, _OCI_INDEX)


def test_allows_a_non_index_media_type() -> None:
    """A single manifest is never a multi-arch image."""
    manifest = {
        "manifests": [{"platform": {"os": "linux", "architecture": "amd64"}}],
    }
    _MODULE._assert_safe_to_protect(manifest, _OCI_MANIFEST)


@pytest.mark.parametrize("children", [None, "nope", 7, {}])
def test_allows_an_index_whose_manifests_key_is_absent_or_wrong(
    children: object,
) -> None:
    """A malformed index is not evidence of a live image; do not over-block."""
    manifest: dict[str, object] = {} if children is None else {"manifests": children}
    _MODULE._assert_safe_to_protect(manifest, _OCI_INDEX)


def test_skips_children_that_are_not_objects() -> None:
    """A junk child must not crash the guard."""
    manifest = {"manifests": ["nope", 7, None]}
    _MODULE._assert_safe_to_protect(manifest, _OCI_INDEX)


# ── digest validation ───────────────────────────────────────────


def test_digest_regex_accepts_a_clean_digest() -> None:
    assert _MODULE._DIGEST_RE.fullmatch("sha256:" + "a" * 64) is not None


@pytest.mark.parametrize(
    "value",
    [
        "sha256:" + "a" * 64 + "\n",
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
        "md5:" + "a" * 64,
        "",
    ],
)
def test_digest_regex_rejects_malformed_values(value: str) -> None:
    """`fullmatch` closes the trailing-newline hole that `match` left open."""
    assert _MODULE._DIGEST_RE.fullmatch(value) is None


def test_main_rejects_a_digest_with_a_trailing_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A digest pasted out of a tracking issue can carry whitespace."""
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    argv = [
        "--owner",
        "Owner",
        "--package",
        "synthorg-sidecar-base",
        "--digest",
        "sha256:" + "a" * 64 + "\n",
    ]
    assert _MODULE.main(argv) == 2


# ── _assert_registry_url ────────────────────────────────────────


def test_registry_url_accepts_the_registry() -> None:
    _MODULE._assert_registry_url("https://ghcr.io/v2/owner/pkg/manifests/sha256:a")


@pytest.mark.parametrize(
    "url",
    [
        "https://ghcr.io.evil.example/v2/x",
        "https://ghcr.io@evil.example/v2/x",
        "https://evil.example/v2/x",
        "http://ghcr.io/v2/x",
        "file:///etc/passwd",
    ],
)
def test_registry_url_refuses_anything_else(url: str) -> None:
    """Host comparison, not a prefix: the first two pass a startswith check."""
    with pytest.raises(_MODULE.GhcrProtectionError):
        _MODULE._assert_registry_url(url)


# ── _request retry ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the retry ladder from actually waiting."""
    monkeypatch.setattr(_MODULE.time, "sleep", lambda _seconds: None)


def _responses(
    monkeypatch: pytest.MonkeyPatch, statuses: list[int]
) -> list[tuple[int, ...]]:
    calls: list[tuple[int, ...]] = []
    remaining = iter(statuses)

    def fake_once(_url: str, **_kwargs: object) -> tuple[int, bytes, dict[str, str]]:
        status = next(remaining)
        calls.append((status,))
        return status, b"{}", {}

    monkeypatch.setattr(_MODULE, "_request_once", fake_once)
    return calls


def test_transient_status_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GHCR returns spurious 401s under the load this tool runs against."""
    calls = _responses(monkeypatch, [401, 200])
    status, _, _ = _MODULE._request("https://ghcr.io/v2/x", authorization="Bearer t")
    assert status == _MODULE._HTTP_OK
    assert len(calls) == 2


def test_server_error_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _responses(monkeypatch, [503, 503, 200])
    status, _, _ = _MODULE._request("https://ghcr.io/v2/x", authorization="Bearer t")
    assert status == _MODULE._HTTP_OK
    assert len(calls) == 3


def test_definitive_client_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 403 is a real permission problem; retrying only delays the diagnosis."""
    calls = _responses(monkeypatch, [403])
    status, _, _ = _MODULE._request("https://ghcr.io/v2/x", authorization="Bearer t")
    assert status == 403
    assert len(calls) == 1


def test_exhausted_retries_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _responses(monkeypatch, [500] * _MODULE._RETRY_ATTEMPTS)
    status, _, _ = _MODULE._request("https://ghcr.io/v2/x", authorization="Bearer t")
    assert status == 500


def test_network_error_is_retried_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []

    reason = "connection reset"

    def always_fails(_url: str, **_kwargs: object) -> tuple[int, bytes, dict[str, str]]:
        attempts.append(1)
        raise urllib.error.URLError(reason)

    monkeypatch.setattr(_MODULE, "_request_once", always_fails)
    with pytest.raises(_MODULE.GhcrProtectionError):
        _MODULE._request("https://ghcr.io/v2/x", authorization="Bearer t")
    assert len(attempts) == _MODULE._RETRY_ATTEMPTS


# ── _registry_token ─────────────────────────────────────────────


def test_token_response_that_is_not_json_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This tool runs when GHCR is already misbehaving; a raw traceback hides that."""

    def fake_request(_url: str, **_kwargs: object) -> tuple[int, bytes, dict[str, str]]:
        return _MODULE._HTTP_OK, b"<html>gateway</html>", {}

    monkeypatch.setattr(_MODULE, "_request", fake_request)
    with pytest.raises(_MODULE.GhcrProtectionError):
        _MODULE._registry_token("Owner", "pkg", github_token="t", push=False)


def test_token_response_without_a_token_field_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request(_url: str, **_kwargs: object) -> tuple[int, bytes, dict[str, str]]:
        return _MODULE._HTTP_OK, b'{"errors": []}', {}

    monkeypatch.setattr(_MODULE, "_request", fake_request)
    with pytest.raises(_MODULE.GhcrProtectionError):
        _MODULE._registry_token("Owner", "pkg", github_token="t", push=False)
