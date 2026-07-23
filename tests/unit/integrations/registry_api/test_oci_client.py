"""Unit tests for the OCI Distribution v2 registry client.

Covers the structural egress pin (every path resolves relative to the pinned
base URL, redirects are not followed, a blob-upload location cannot leave the
host), the bearer-token challenge flow and its realm-host validation (the one
place a credential could leave the pinned origin), Basic-auth fallback,
status-to-typed-error mapping, and the factory's HTTPS requirement.
"""

import base64
import hashlib

import httpx
import pytest
import respx

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.registry_target import RegistryProvider
from synthorg.integrations.errors import (
    RegistryApiAuthError,
    RegistryApiClientError,
    RegistryApiError,
    RegistryApiRateLimitError,
)
from synthorg.integrations.registry_api import build_registry_api_client
from synthorg.integrations.registry_api.oci import OciRegistryClient

pytestmark = pytest.mark.unit

_HOST = "https://registry.example.com"
_REPO = "org/app"
_MANIFEST = b'{"schemaVersion":2,"config":{},"layers":[]}'
_DIGEST = "sha256:" + "a" * 64
# The genuine content digest of _MANIFEST, for the fetch-by-digest path.
_REAL_DIGEST = "sha256:" + hashlib.sha256(_MANIFEST).hexdigest()


def _client(
    *,
    base_url: str = _HOST,
    username: str = "robot",
    auth_host: str = "",
) -> OciRegistryClient:
    return OciRegistryClient(
        api_base_url=base_url,
        repository=NotBlankStr(_REPO),
        username=username,
        token="t0ken",
        timeout=5.0,
        auth_host=auth_host,
    )


class TestReads:
    @respx.mock
    async def test_list_tags(self) -> None:
        route = respx.get(f"{_HOST}/v2/{_REPO}/tags/list").mock(
            return_value=httpx.Response(200, json={"name": _REPO, "tags": ["v1", "v2"]})
        )
        async with _client() as client:
            result = await client.list_tags(limit=10)
        assert route.call_count == 1
        assert result.tags == ("v1", "v2")

    @respx.mock
    async def test_null_tag_list_is_empty(self) -> None:
        respx.get(f"{_HOST}/v2/{_REPO}/tags/list").mock(
            return_value=httpx.Response(200, json={"name": _REPO, "tags": None})
        )
        async with _client() as client:
            result = await client.list_tags(limit=10)
        assert result.tags == ()

    @pytest.mark.parametrize("bad", ["v1", 123, {"a": 1}], ids=["string", "int", "obj"])
    @respx.mock
    async def test_non_list_tags_is_malformed(self, bad: object) -> None:
        # A bare string would otherwise iterate into single-character tags.
        respx.get(f"{_HOST}/v2/{_REPO}/tags/list").mock(
            return_value=httpx.Response(200, json={"name": _REPO, "tags": bad})
        )
        async with _client() as client:
            with pytest.raises(RegistryApiError):
                await client.list_tags(limit=10)

    @respx.mock
    async def test_get_manifest_by_digest_verifies_content(self) -> None:
        respx.get(f"{_HOST}/v2/{_REPO}/manifests/{_REAL_DIGEST}").mock(
            return_value=httpx.Response(200, content=_MANIFEST)
        )
        async with _client() as client:
            ref = await client.get_manifest(reference=NotBlankStr(_REAL_DIGEST))
        assert str(ref.digest) == _REAL_DIGEST

    @respx.mock
    async def test_get_manifest_by_digest_mismatch_is_refused(self) -> None:
        # The content does not hash to the requested digest: refuse it rather
        # than let a promote republish content that is not what was addressed.
        respx.get(f"{_HOST}/v2/{_REPO}/manifests/{_DIGEST}").mock(
            return_value=httpx.Response(200, content=_MANIFEST)
        )
        async with _client() as client:
            with pytest.raises(RegistryApiError):
                await client.get_manifest(reference=NotBlankStr(_DIGEST))

    @respx.mock
    async def test_get_manifest_uses_digest_header(self) -> None:
        respx.get(f"{_HOST}/v2/{_REPO}/manifests/v1").mock(
            return_value=httpx.Response(
                200,
                content=_MANIFEST,
                headers={
                    "Docker-Content-Digest": _DIGEST,
                    "Content-Type": "application/vnd.oci.image.manifest.v1+json",
                },
            )
        )
        async with _client() as client:
            ref = await client.get_manifest(reference=NotBlankStr("v1"))
        assert str(ref.digest) == _DIGEST
        assert ref.raw == _MANIFEST

    @respx.mock
    async def test_get_manifest_computes_digest_when_header_absent(self) -> None:
        respx.get(f"{_HOST}/v2/{_REPO}/manifests/v1").mock(
            return_value=httpx.Response(200, content=_MANIFEST)
        )
        async with _client() as client:
            ref = await client.get_manifest(reference=NotBlankStr("v1"))
        assert str(ref.digest).startswith("sha256:")


class TestWrites:
    @respx.mock
    async def test_put_manifest_returns_stored_digest(self) -> None:
        route = respx.put(f"{_HOST}/v2/{_REPO}/manifests/latest").mock(
            return_value=httpx.Response(201, headers={"Docker-Content-Digest": _DIGEST})
        )
        async with _client() as client:
            ref = await client.put_manifest(
                tag=NotBlankStr("latest"),
                raw=_MANIFEST,
                media_type="application/vnd.oci.image.manifest.v1+json",
            )
        assert route.call_count == 1
        assert str(ref.digest) == _DIGEST

    @respx.mock
    async def test_blob_exists_true_and_false(self) -> None:
        respx.head(f"{_HOST}/v2/{_REPO}/blobs/{_DIGEST}").mock(
            return_value=httpx.Response(200)
        )
        async with _client() as client:
            assert await client.blob_exists(digest=NotBlankStr(_DIGEST)) is True
        respx.head(f"{_HOST}/v2/{_REPO}/blobs/{_DIGEST}").mock(
            return_value=httpx.Response(404)
        )
        async with _client() as client:
            assert await client.blob_exists(digest=NotBlankStr(_DIGEST)) is False

    @respx.mock
    async def test_upload_blob_monolithic(self) -> None:
        location = f"{_HOST}/v2/{_REPO}/blobs/uploads/session-1"
        respx.post(f"{_HOST}/v2/{_REPO}/blobs/uploads/").mock(
            return_value=httpx.Response(202, headers={"Location": location})
        )
        put_route = respx.put(location).mock(return_value=httpx.Response(201))
        async with _client() as client:
            await client.upload_blob(digest=NotBlankStr(_DIGEST), data=b"layerbytes")
        assert put_route.call_count == 1

    @respx.mock
    async def test_upload_location_off_host_is_refused(self) -> None:
        respx.post(f"{_HOST}/v2/{_REPO}/blobs/uploads/").mock(
            return_value=httpx.Response(
                202, headers={"Location": "https://evil.example.com/steal"}
            )
        )
        async with _client() as client:
            with pytest.raises(RegistryApiError):
                await client.upload_blob(digest=NotBlankStr(_DIGEST), data=b"x")

    @respx.mock
    async def test_upload_location_different_port_is_refused(self) -> None:
        # Same host, different port: still off the pinned origin.
        respx.post(f"{_HOST}/v2/{_REPO}/blobs/uploads/").mock(
            return_value=httpx.Response(
                202,
                headers={"Location": "https://registry.example.com:8443/up"},
            )
        )
        async with _client() as client:
            with pytest.raises(RegistryApiError):
                await client.upload_blob(digest=NotBlankStr(_DIGEST), data=b"x")


class TestAuthFlow:
    @respx.mock
    async def test_bearer_challenge_exchange(self) -> None:
        tags_url = f"{_HOST}/v2/{_REPO}/tags/list"
        challenge = (
            'Bearer realm="https://registry.example.com/token",'
            'service="registry.example.com",scope="repository:org/app:pull"'
        )
        respx.get(tags_url).mock(
            side_effect=[
                httpx.Response(401, headers={"WWW-Authenticate": challenge}),
                httpx.Response(200, json={"name": _REPO, "tags": ["v1"]}),
            ]
        )
        token_route = respx.get("https://registry.example.com/token").mock(
            return_value=httpx.Response(200, json={"token": "BEARER123"})
        )
        async with _client() as client:
            result = await client.list_tags(limit=10)
        assert result.tags == ("v1",)
        # The token endpoint received the Basic credential, never the bearer.
        sent = token_route.calls.last.request.headers["authorization"]
        assert sent.startswith("Basic ")
        assert base64.b64decode(sent.split(" ", 1)[1]).decode() == "robot:t0ken"

    @respx.mock
    async def test_realm_on_disallowed_host_refuses(self) -> None:
        challenge = 'Bearer realm="https://evil.example.com/token",service="x"'
        respx.get(f"{_HOST}/v2/{_REPO}/tags/list").mock(
            return_value=httpx.Response(401, headers={"WWW-Authenticate": challenge})
        )
        async with _client() as client:
            with pytest.raises(RegistryApiAuthError):
                await client.list_tags(limit=10)

    @respx.mock
    async def test_realm_on_same_host_different_port_refuses(self) -> None:
        # The pinned origin is port 443; a realm on another port is off-origin.
        challenge = 'Bearer realm="https://registry.example.com:8443/token",service="x"'
        respx.get(f"{_HOST}/v2/{_REPO}/tags/list").mock(
            return_value=httpx.Response(401, headers={"WWW-Authenticate": challenge})
        )
        async with _client() as client:
            with pytest.raises(RegistryApiAuthError):
                await client.list_tags(limit=10)

    @respx.mock
    async def test_repeated_401_re_auths_once_without_looping(self) -> None:
        # A bearer exchange followed by a still-401 retry must surface an auth
        # error after exactly two requests, never loop re-exchanging the token.
        challenge = 'Bearer realm="https://registry.example.com/token",service="x"'
        route = respx.get(f"{_HOST}/v2/{_REPO}/tags/list").mock(
            return_value=httpx.Response(401, headers={"WWW-Authenticate": challenge})
        )
        token_route = respx.get("https://registry.example.com/token").mock(
            return_value=httpx.Response(200, json={"token": "B"})
        )
        async with _client() as client:
            with pytest.raises(RegistryApiAuthError):
                await client.list_tags(limit=10)
        assert route.call_count == 2
        assert token_route.call_count == 1

    @respx.mock
    async def test_realm_on_declared_auth_host_is_allowed(self) -> None:
        base = "https://registry-1.docker.io"
        tags_url = f"{base}/v2/{_REPO}/tags/list"
        challenge = 'Bearer realm="https://auth.docker.io/token",service="registry"'
        respx.get(tags_url).mock(
            side_effect=[
                httpx.Response(401, headers={"WWW-Authenticate": challenge}),
                httpx.Response(200, json={"name": _REPO, "tags": []}),
            ]
        )
        respx.get("https://auth.docker.io/token").mock(
            return_value=httpx.Response(200, json={"access_token": "B"})
        )
        async with _client(base_url=base, auth_host="auth.docker.io") as client:
            result = await client.list_tags(limit=10)
        assert result.tags == ()

    @respx.mock
    async def test_basic_fallback_on_non_bearer_challenge(self) -> None:
        tags_url = f"{_HOST}/v2/{_REPO}/tags/list"
        respx.get(tags_url).mock(
            side_effect=[
                httpx.Response(401, headers={"WWW-Authenticate": 'Basic realm="r"'}),
                httpx.Response(200, json={"name": _REPO, "tags": ["v1"]}),
            ]
        )
        async with _client() as client:
            result = await client.list_tags(limit=10)
        assert result.tags == ("v1",)


class TestStatusMapping:
    @respx.mock
    async def test_rate_limited(self) -> None:
        respx.get(f"{_HOST}/v2/{_REPO}/tags/list").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "12"})
        )
        async with _client() as client:
            with pytest.raises(RegistryApiRateLimitError) as exc:
                await client.list_tags(limit=10)
        assert exc.value.retry_after_seconds == 12.0

    @respx.mock
    async def test_client_error_is_non_retryable(self) -> None:
        respx.get(f"{_HOST}/v2/{_REPO}/tags/list").mock(
            return_value=httpx.Response(400, json={"errors": [{"message": "bad"}]})
        )
        async with _client() as client:
            with pytest.raises(RegistryApiClientError):
                await client.list_tags(limit=10)

    @respx.mock
    async def test_server_error_is_retryable(self) -> None:
        respx.get(f"{_HOST}/v2/{_REPO}/tags/list").mock(
            return_value=httpx.Response(503)
        )
        async with _client() as client:
            with pytest.raises(RegistryApiError):
                await client.list_tags(limit=10)


class TestFactory:
    def test_non_https_base_url_is_refused(self) -> None:
        with pytest.raises(RegistryApiError):
            build_registry_api_client(
                provider=RegistryProvider.GENERIC_OCI,
                base_url="http://insecure.example.com",
                repository=NotBlankStr(_REPO),
                username="",
                token="t",
                timeout=5.0,
                auth_host="",
            )

    def test_supported_provider_builds(self) -> None:
        client = build_registry_api_client(
            provider=RegistryProvider.GENERIC_OCI,
            base_url=_HOST,
            repository=NotBlankStr(_REPO),
            username="",
            token="t",
            timeout=5.0,
            auth_host="",
        )
        assert isinstance(client, OciRegistryClient)
