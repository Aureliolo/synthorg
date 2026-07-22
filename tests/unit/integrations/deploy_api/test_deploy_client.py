"""Unit tests for the deploy-platform API client layer.

Covers the structural egress pin (every path resolves relative to the
configured base URL, and a leading slash cannot escape it), platform
state normalisation, status-to-typed-error mapping, and the factory's
HTTPS requirement.
"""

import httpx
import pytest
import respx

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.deploy_target import DeployPlatform
from synthorg.integrations.deploy_api import (
    DeployState,
    build_deploy_api_client,
    deploy_api_supported,
)
from synthorg.integrations.deploy_api.vercel import VercelDeployClient
from synthorg.integrations.errors import (
    DeployApiAuthError,
    DeployApiError,
    DeployApiRateLimitError,
)

pytestmark = pytest.mark.unit

_HOST = "https://api.example-deploy.com"


def _client(*, base_url: str = _HOST) -> VercelDeployClient:
    return VercelDeployClient(
        api_base_url=base_url,
        token="t0ken",
        timeout=5.0,
        project=NotBlankStr("acme-web"),
    )


class TestEgressPin:
    @respx.mock
    async def test_every_call_stays_on_the_configured_host(self) -> None:
        """respx only mocks this host, so any other origin fails to match."""
        route = respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(
                200, json={"id": "dpl_1", "readyState": "READY"}
            )
        )
        client = _client()
        try:
            await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))
        finally:
            await client.aclose()
        assert route.call_count == 1

    @respx.mock
    async def test_base_url_path_prefix_is_preserved(self) -> None:
        """A self-hosted control plane under a path prefix stays under it."""
        route = respx.get(
            "https://selfhosted.example.com/deploy/v13/deployments/dpl_1"
        ).mock(
            return_value=httpx.Response(
                200, json={"id": "dpl_1", "readyState": "READY"}
            )
        )
        client = _client(base_url="https://selfhosted.example.com/deploy")
        try:
            await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))
        finally:
            await client.aclose()
        assert route.call_count == 1

    @respx.mock
    async def test_redirects_are_not_followed(self) -> None:
        """A 3xx must not carry the Authorization header off the pinned host."""
        respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(
                302, headers={"location": "https://evil.example.com/steal"}
            )
        )
        client = _client()
        try:
            with pytest.raises(DeployApiError):
                await client.trigger_deployment(git_ref="main")
        finally:
            await client.aclose()


class TestStateNormalisation:
    @respx.mock
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("QUEUED", DeployState.QUEUED),
            ("BUILDING", DeployState.BUILDING),
            ("INITIALIZING", DeployState.BUILDING),
            ("READY", DeployState.READY),
            ("ERROR", DeployState.FAILED),
            ("CANCELED", DeployState.CANCELLED),
        ],
    )
    async def test_platform_states_map_onto_neutral_leaves(
        self, raw: str, expected: DeployState
    ) -> None:
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(200, json={"id": "dpl_1", "readyState": raw})
        )
        client = _client()
        try:
            deployment = await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))
        finally:
            await client.aclose()
        assert deployment.state is expected

    @respx.mock
    async def test_unknown_state_is_treated_as_in_flight(self) -> None:
        """An unrecognised state must never read as a finished success."""
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(
                200, json={"id": "dpl_1", "readyState": "WARP_DRIVE"}
            )
        )
        client = _client()
        try:
            deployment = await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))
        finally:
            await client.aclose()
        assert deployment.state is DeployState.QUEUED


class TestStatusMapping:
    @respx.mock
    async def test_rate_limit_carries_retry_after(self) -> None:
        respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(429, headers={"retry-after": "12"})
        )
        client = _client()
        try:
            with pytest.raises(DeployApiRateLimitError) as caught:
                await client.trigger_deployment(git_ref="main")
        finally:
            await client.aclose()
        assert caught.value.retry_after_seconds == 12.0

    @respx.mock
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_failures_map_to_auth_error(self, status: int) -> None:
        respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(status, json={"error": {"message": "nope"}})
        )
        client = _client()
        try:
            with pytest.raises(DeployApiAuthError):
                await client.trigger_deployment(git_ref="main")
        finally:
            await client.aclose()

    @respx.mock
    async def test_missing_id_is_rejected(self) -> None:
        respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(200, json={"readyState": "READY"})
        )
        client = _client()
        try:
            with pytest.raises(DeployApiError):
                await client.trigger_deployment(git_ref="main")
        finally:
            await client.aclose()


class TestFactory:
    def test_vercel_is_wired(self) -> None:
        assert deploy_api_supported(DeployPlatform.VERCEL) is True

    def test_plain_http_base_url_is_refused(self) -> None:
        """Plain HTTP would put the platform token on the wire in clear."""
        with pytest.raises(DeployApiError):
            build_deploy_api_client(
                platform=DeployPlatform.VERCEL,
                base_url="http://api.example-deploy.com",
                token="t0ken",
                timeout=5.0,
                project=NotBlankStr("acme-web"),
            )

    def test_blank_base_url_is_refused(self) -> None:
        with pytest.raises(DeployApiError):
            build_deploy_api_client(
                platform=DeployPlatform.VERCEL,
                base_url="",
                token="t0ken",
                timeout=5.0,
                project=NotBlankStr("acme-web"),
            )

    def test_builds_a_client_for_a_wired_platform(self) -> None:
        client = build_deploy_api_client(
            platform=DeployPlatform.VERCEL,
            base_url=_HOST,
            token="t0ken",
            timeout=5.0,
            project=NotBlankStr("acme-web"),
        )
        assert isinstance(client, VercelDeployClient)
