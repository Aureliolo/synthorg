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
from synthorg.integrations.connections.deploy_target import (
    DeployEnvironment,
    DeployPlatform,
)
from synthorg.integrations.deploy_api import (
    DeployState,
    build_deploy_api_client,
    deploy_api_supported,
)
from synthorg.integrations.deploy_api.vercel import VercelDeployClient
from synthorg.integrations.errors import (
    DeployApiAuthError,
    DeployApiClientError,
    DeployApiError,
    DeployApiRateLimitError,
)

pytestmark = pytest.mark.unit

_HOST = "https://api.example-deploy.com"


def _client(
    *,
    base_url: str = _HOST,
    environment: DeployEnvironment = DeployEnvironment.PRODUCTION,
) -> VercelDeployClient:
    return VercelDeployClient(
        api_base_url=base_url,
        token="t0ken",
        timeout=5.0,
        project=NotBlankStr("acme-web"),
        environment=environment,
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
        async with _client() as client:
            await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))
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
        async with _client(base_url="https://selfhosted.example.com/deploy") as client:
            await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))
        assert route.call_count == 1

    @respx.mock
    async def test_redirects_are_not_followed(self) -> None:
        """A 3xx must not carry the Authorization header off the pinned host."""
        route = respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(
                302, headers={"location": "https://evil.example.com/steal"}
            )
        )
        async with _client() as client:
            with pytest.raises(DeployApiError):
                await client.trigger_deployment(git_ref="main")
        # The redirect target is never requested: exactly one hop is made.
        assert route.call_count == 1


class TestTargetBinding:
    @respx.mock
    @pytest.mark.parametrize(
        ("environment", "expected_target"),
        [
            (DeployEnvironment.PRODUCTION, "production"),
            (DeployEnvironment.STAGING, "staging"),
        ],
    )
    async def test_environment_decides_the_vendor_target(
        self, environment: DeployEnvironment, expected_target: str
    ) -> None:
        """A staging client can never emit a production release."""
        route = respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(
                200, json={"id": "dpl_1", "readyState": "QUEUED"}
            )
        )
        async with _client(environment=environment) as client:
            await client.trigger_deployment(git_ref="main")
        body = route.calls.last.request.content
        assert f'"target":"{expected_target}"' in body.decode()

    @respx.mock
    @pytest.mark.parametrize(
        ("environment", "expected_target"),
        [
            (DeployEnvironment.PRODUCTION, "production"),
            (DeployEnvironment.STAGING, "staging"),
        ],
    )
    async def test_list_is_scoped_to_the_bound_environment(
        self, environment: DeployEnvironment, expected_target: str
    ) -> None:
        """A staging client must not be able to enumerate production."""
        route = respx.get(f"{_HOST}/v6/deployments").mock(
            return_value=httpx.Response(200, json={"deployments": []})
        )
        async with _client(environment=environment) as client:
            await client.list_deployments(limit=5)
        assert route.calls.last.request.url.params["target"] == expected_target


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
        async with _client() as client:
            deployment = await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))
        assert deployment.state is expected

    @respx.mock
    async def test_unknown_state_is_treated_as_in_flight(self) -> None:
        """An unrecognised state must never read as a finished success."""
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(
                200, json={"id": "dpl_1", "readyState": "WARP_DRIVE"}
            )
        )
        async with _client() as client:
            deployment = await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))
        assert deployment.state is DeployState.QUEUED


class TestStatusMapping:
    @respx.mock
    async def test_rate_limit_carries_retry_after(self) -> None:
        respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(429, headers={"retry-after": "12"})
        )
        async with _client() as client:
            with pytest.raises(DeployApiRateLimitError) as caught:
                await client.trigger_deployment(git_ref="main")
        assert caught.value.retry_after_seconds == 12.0

    @respx.mock
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_failures_map_to_auth_error(self, status: int) -> None:
        respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(status, json={"error": {"message": "nope"}})
        )
        async with _client() as client:
            with pytest.raises(DeployApiAuthError):
                await client.trigger_deployment(git_ref="main")

    @respx.mock
    async def test_missing_id_is_rejected(self) -> None:
        respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(200, json={"readyState": "READY"})
        )
        async with _client() as client:
            with pytest.raises(DeployApiError):
                await client.trigger_deployment(git_ref="main")

    @respx.mock
    async def test_non_json_body_maps_to_deploy_error(self) -> None:
        """A 2xx body that is not JSON fails loudly, not as an empty result."""
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(200, text="<html>proxy error</html>")
        )
        async with _client() as client:
            with pytest.raises(DeployApiError):
                await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))

    @respx.mock
    async def test_non_list_log_body_maps_to_deploy_error(self) -> None:
        """A debugging read must not report 'no logs' when the fetch failed."""
        respx.get(f"{_HOST}/v3/deployments/dpl_1/events").mock(
            return_value=httpx.Response(200, json={"unexpected": "shape"})
        )
        async with _client() as client:
            with pytest.raises(DeployApiError):
                await client.get_deployment_logs(
                    deployment_id=NotBlankStr("dpl_1"), limit=10
                )

    @respx.mock
    async def test_deterministic_4xx_is_classified_non_retryable(self) -> None:
        """A 404 will not succeed on a bare retry, so it is not transient."""
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(404, json={"error": {"message": "gone"}})
        )
        async with _client() as client:
            with pytest.raises(DeployApiClientError):
                await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))

    @respx.mock
    async def test_server_error_stays_retryable(self) -> None:
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(503)
        )
        async with _client() as client:
            with pytest.raises(DeployApiError) as caught:
                await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))
        assert not isinstance(caught.value, DeployApiClientError)

    @respx.mock
    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(500, text="<html>gateway</html>"),
            httpx.Response(500, json={"unexpected": "shape"}),
        ],
        ids=["non-json-body", "no-error-field"],
    )
    async def test_error_body_without_a_usable_detail_still_raises(
        self, response: httpx.Response
    ) -> None:
        """The detail extraction must never mask the failure itself."""
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(return_value=response)
        async with _client() as client:
            with pytest.raises(DeployApiError):
                await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))

    @respx.mock
    async def test_transport_failure_maps_to_deploy_error(self) -> None:
        """A connection failure must not escape as a raw httpx error."""
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            side_effect=httpx.ConnectError("no route")
        )
        async with _client() as client:
            with pytest.raises(DeployApiError):
                await client.get_deployment(deployment_id=NotBlankStr("dpl_1"))

    @respx.mock
    async def test_list_without_a_deployment_array_maps_to_deploy_error(self) -> None:
        """An unparseable list must not read as 'no deployments'."""
        respx.get(f"{_HOST}/v6/deployments").mock(
            return_value=httpx.Response(200, json={"unexpected": "shape"})
        )
        async with _client() as client:
            with pytest.raises(DeployApiError):
                await client.list_deployments(limit=5)

    @respx.mock
    async def test_non_object_deployment_entry_is_rejected(self) -> None:
        respx.get(f"{_HOST}/v6/deployments").mock(
            return_value=httpx.Response(200, json={"deployments": ["dpl_1"]})
        )
        async with _client() as client:
            with pytest.raises(DeployApiError):
                await client.list_deployments(limit=5)

    @respx.mock
    async def test_blank_log_lines_are_dropped(self) -> None:
        """An empty event carries no content, so it is not emitted."""
        respx.get(f"{_HOST}/v3/deployments/dpl_1/events").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"created": 1, "text": "building"},
                    {"created": 2, "text": "   "},
                    {"created": 3, "text": ""},
                ],
            )
        )
        async with _client() as client:
            lines = await client.get_deployment_logs(
                deployment_id=NotBlankStr("dpl_1"), limit=10
            )
        assert [line.text for line in lines] == ["building"]

    @respx.mock
    async def test_nested_payload_events_are_read_and_non_objects_skipped(self) -> None:
        """The platform nests log text under ``payload`` on some event kinds."""
        respx.get(f"{_HOST}/v3/deployments/dpl_1/events").mock(
            return_value=httpx.Response(
                200,
                json=[
                    "not-an-event",
                    {"date": 1, "payload": {"text": "compiling"}},
                    {"date": 2, "payload": {"no_text": True}},
                ],
            )
        )
        async with _client() as client:
            lines = await client.get_deployment_logs(
                deployment_id=NotBlankStr("dpl_1"), limit=10
            )
        assert [line.text for line in lines] == ["compiling"]


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
                environment=DeployEnvironment.PRODUCTION,
            )

    def test_blank_base_url_is_refused(self) -> None:
        with pytest.raises(DeployApiError):
            build_deploy_api_client(
                platform=DeployPlatform.VERCEL,
                base_url="",
                token="t0ken",
                timeout=5.0,
                project=NotBlankStr("acme-web"),
                environment=DeployEnvironment.PRODUCTION,
            )

    def test_builds_a_client_for_a_wired_platform(self) -> None:
        client = build_deploy_api_client(
            platform=DeployPlatform.VERCEL,
            base_url=_HOST,
            token="t0ken",
            timeout=5.0,
            project=NotBlankStr("acme-web"),
            environment=DeployEnvironment.PRODUCTION,
        )
        assert isinstance(client, VercelDeployClient)
        assert client.project == "acme-web"
