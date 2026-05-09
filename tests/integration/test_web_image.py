"""Integration tests for the Caddy-based web image.

Validates that the apko-composed web image correctly serves the React SPA,
pre-compressed assets, static docs, and applies per-request CSP nonces.
These tests require a locally-built web image (docker load from apko output).
"""

import os
import re
import subprocess
import time
from collections.abc import Generator

import httpx
import pytest

from synthorg.observability import get_logger

logger = get_logger(__name__)

_IMAGE_REF_PATTERN = re.compile(r"^[a-zA-Z0-9][\w.:/@-]*$")
_SAFE_NAME_PATTERN = re.compile(r"^[\w-]+$")
WEB_IMAGE = os.environ.get("SYNTHORG_WEB_IMAGE", "ghcr.io/aureliolo/synthorg-web:test")
if not _IMAGE_REF_PATTERN.match(WEB_IMAGE):
    msg = f"Invalid image reference: {WEB_IMAGE}"
    raise ValueError(msg)


def _wait_for_web_container_ready(
    base_url: str, *, deadline_secs: float = 15.0
) -> None:
    """Poll ``base_url`` until it returns 200, capped by a wall-clock deadline.

    A wall-clock deadline (rather than a fixed iteration count) bounds
    total wait independently of per-request timeout interactions; the
    inner ``httpx.get`` timeout is shrunk on each iteration so the very
    last request cannot push the budget past the deadline.
    """
    deadline = time.time() + deadline_secs
    while time.time() < deadline:
        remaining = deadline - time.time()
        try:
            resp = httpx.get(f"{base_url}/", timeout=min(2, remaining))
        except httpx.ConnectError, httpx.ReadError:
            pass
        else:
            if resp.status_code == 200:
                return
        time.sleep(0.25)
    pytest.fail(f"Web container did not become healthy within {deadline_secs:.0f}s")


@pytest.fixture(scope="module")
def web_container() -> Generator[str]:
    """Start the web image with Docker-assigned port and yield the base URL."""
    docker = "docker"
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    container_name = f"synthorg-web-test-{worker}-{os.getpid()}"
    if not _SAFE_NAME_PATTERN.match(container_name):
        msg = f"Invalid container name: {container_name}"
        raise ValueError(msg)
    started = False
    try:
        cmd = [
            docker,
            "run",
            "-d",
            "--name",
            container_name,
            "-p",
            "127.0.0.1::8080",
            "--read-only",
            "--tmpfs",
            "/tmp:noexec,nosuid,nodev,size=16m",  # noqa: S108
            "--tmpfs",
            "/config/caddy:noexec,nosuid,nodev,size=8m",
            "--tmpfs",
            "/data/caddy:noexec,nosuid,nodev,size=16m",
            WEB_IMAGE,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)  # noqa: S603
            started = True
        except FileNotFoundError:
            pytest.skip("Docker binary not found on PATH")
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.lower()
            image_missing = (
                "manifest unknown" in stderr
                or "pull access denied" in stderr
                or "repository does not exist" in stderr
                or "failed to resolve reference" in stderr
                or "unable to find image" in stderr
                # Docker's "Unable to find image" message collapses to
                # "manifest for ... not found" on some daemons; keep the
                # combined "manifest ... not found" match so we skip on
                # that exact variant but NOT on unrelated runtime failures
                # (e.g. missing entrypoint inside the image).
                or ("manifest for" in stderr and "not found" in stderr)
            )
            if image_missing:
                pytest.skip(f"Web image not available: {exc.stderr.strip()}")
            if (
                "cannot connect to the docker daemon" in stderr
                or "is the docker daemon running" in stderr
                or "error during connect" in stderr
            ):
                pytest.skip(f"Docker daemon unavailable: {exc.stderr.strip()}")
            pytest.fail(f"Web container failed to start: {exc.stderr}")

        fmt = '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}'
        port_info = subprocess.run(  # noqa: S603
            [docker, "inspect", "--format", fmt, container_name],
            check=True,
            capture_output=True,
            text=True,
        )
        host_port = port_info.stdout.strip()
        base_url = f"http://127.0.0.1:{host_port}"
        # Polls a real Docker container's HTTP endpoint, so the wait is
        # genuine network I/O against an external process; no FakeClock
        # seam applies. See ``_wait_for_web_container_ready`` for the
        # wall-clock deadline budget.
        _wait_for_web_container_ready(base_url)

        yield base_url
    finally:
        if started:
            subprocess.run(  # noqa: S603
                [docker, "rm", "-f", container_name],
                capture_output=True,
                check=False,
            )


@pytest.mark.integration
@pytest.mark.slow
class TestWebImage:
    def test_root_returns_200(self, web_container: str) -> None:
        resp = httpx.get(f"{web_container}/")
        assert resp.status_code == 200

    def test_csp_nonce_present_in_header(self, web_container: str) -> None:
        resp = httpx.get(f"{web_container}/")
        csp = resp.headers.get("content-security-policy", "")
        match = re.search(r"nonce-([a-f0-9-]+)", csp)
        assert match, f"No nonce found in CSP header: {csp}"

    def test_csp_nonce_matches_body(self, web_container: str) -> None:
        resp = httpx.get(f"{web_container}/")
        csp = resp.headers.get("content-security-policy", "")
        header_nonce = re.search(r"nonce-([a-f0-9-]+)", csp)
        assert header_nonce

        body_match = re.search(
            r'content="([a-f0-9-]+)"',
            resp.text,
        )
        assert body_match, "No nonce found in response body meta tag"
        assert header_nonce.group(1) == body_match.group(1)

    def test_csp_nonce_changes_per_request(self, web_container: str) -> None:
        resp1 = httpx.get(f"{web_container}/")
        resp2 = httpx.get(f"{web_container}/")
        nonce1 = re.search(
            r"nonce-([a-f0-9-]+)",
            resp1.headers.get("content-security-policy", ""),
        )
        nonce2 = re.search(
            r"nonce-([a-f0-9-]+)",
            resp2.headers.get("content-security-policy", ""),
        )
        assert nonce1
        assert nonce2
        assert nonce1.group(1) != nonce2.group(1), "Nonce must differ per request"

    def test_docs_has_static_csp(self, web_container: str) -> None:
        resp = httpx.get(f"{web_container}/docs/")
        assert resp.status_code == 200, f"Docs route returned {resp.status_code}"
        csp = resp.headers.get("content-security-policy", "")
        assert "nonce-" not in csp, "Docs CSP must not contain a per-request nonce"
        assert "worker-src 'self' blob:" in csp

    def test_security_headers_present(self, web_container: str) -> None:
        resp = httpx.get(f"{web_container}/")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert "strict-origin" in resp.headers.get("referrer-policy", "")
        assert "63072000" in resp.headers.get("strict-transport-security", "")
        assert "geolocation=()" in resp.headers.get("permissions-policy", "")
        assert resp.headers.get("server") in (None, "")

    def test_spa_fallback(self, web_container: str) -> None:
        resp = httpx.get(f"{web_container}/agents")
        assert resp.status_code == 200
        assert "<div id=" in resp.text or "root" in resp.text

    def test_cache_control_no_cache_on_root(self, web_container: str) -> None:
        resp = httpx.get(f"{web_container}/")
        assert "no-cache" in resp.headers.get("cache-control", "")

    def test_precompressed_asset_served(self, web_container: str) -> None:
        root = httpx.get(f"{web_container}/")
        match = re.search(r'src="(/assets/[^"]+\.js)"', root.text)
        if not match:
            pytest.fail("No hashed JS asset found in root HTML")
        asset_path = match.group(1)
        resp = httpx.get(
            f"{web_container}{asset_path}",
            headers={"Accept-Encoding": "gzip"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-encoding") == "gzip"
        assert "immutable" in resp.headers.get("cache-control", "")
