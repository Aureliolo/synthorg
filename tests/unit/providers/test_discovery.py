# mypy: disable-error-code="explicit-any"
"""Tests for provider model auto-discovery."""

import socket
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from synthorg.providers.discovery import (
    _SsrfCheckResult,
    _validate_discovery_url,
    discover_models,
)
from synthorg.providers.presets import LocalPreset
from synthorg.providers.probing import (
    ProbeResult,
    probe_preset_urls,
)

pytestmark = pytest.mark.unit


def _mock_response(json_data: Any, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "http://test"),
    )


def _mock_client(
    response: httpx.Response | None = None,
    *,
    side_effect: Exception | None = None,
) -> AsyncMock:
    """Build a mock httpx.AsyncClient with async context manager support."""
    client = AsyncMock()
    if side_effect is not None:
        client.get.side_effect = side_effect
    else:
        client.get.return_value = response
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.fixture(autouse=False)
def _bypass_ssrf() -> Generator[None]:
    """Patch SSRF validation so HTTP-behavior tests can use localhost URLs."""
    safe_result = _SsrfCheckResult(error=None, pinned_ip="127.0.0.1")
    with patch(
        "synthorg.providers.discovery._validate_discovery_url",
        return_value=safe_result,
    ):
        yield


@pytest.mark.usefixtures("_bypass_ssrf")
class TestDiscoverOllama:
    """Tests for Ollama model discovery."""

    async def test_parses_response(self) -> None:
        response = _mock_response(
            {
                "models": [
                    {"name": "test-large-001:latest"},
                    {"name": "test-small-001:7b"},
                ],
            }
        )
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            result = await discover_models(
                "http://localhost:11434",
                "ollama",
            )

        assert len(result) == 2
        assert result[0].id == "test-large-001:latest"
        assert result[1].id == "test-small-001:7b"

    async def test_empty_models_list(self) -> None:
        response = _mock_response({"models": []})
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            result = await discover_models(
                "http://localhost:11434",
                "ollama",
            )

        assert result == ()

    async def test_connection_refused(self) -> None:
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(
                side_effect=httpx.ConnectError("refused"),
            )

            result = await discover_models(
                "http://localhost:11434",
                "ollama",
            )

        assert result == ()

    async def test_timeout(self) -> None:
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(
                side_effect=httpx.ReadTimeout("timeout"),
            )

            result = await discover_models(
                "http://localhost:11434",
                "ollama",
            )

        assert result == ()

    async def test_unexpected_structure(self) -> None:
        response = _mock_response({"unexpected": "data"})
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            result = await discover_models(
                "http://localhost:11434",
                "ollama",
            )

        assert result == ()

    async def test_uses_ollama_endpoint(self) -> None:
        response = _mock_response({"models": []})
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            client = _mock_client(response)
            mock_cls.return_value = client

            await discover_models(
                "http://localhost:11434",
                "ollama",
            )

            client.get.assert_called_once_with(
                "http://127.0.0.1:11434/api/tags",
                headers={"Host": "localhost"},
            )

    async def test_trailing_slash_normalized(self) -> None:
        response = _mock_response({"models": []})
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            client = _mock_client(response)
            mock_cls.return_value = client

            await discover_models(
                "http://localhost:11434/",
                "ollama",
            )

            client.get.assert_called_once_with(
                "http://127.0.0.1:11434/api/tags",
                headers={"Host": "localhost"},
            )

    async def test_malformed_entries_skipped(self) -> None:
        """Valid models returned even when some entries are malformed."""
        response = _mock_response(
            {
                "models": [
                    {"name": "test-model-001"},
                    "not-a-dict",
                    {"name": ""},
                    {"no-name-key": True},
                    {"name": "test-model-002"},
                ],
            }
        )
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            result = await discover_models(
                "http://localhost:11434",
                "ollama",
            )

        assert len(result) == 2
        assert result[0].id == "test-model-001"
        assert result[1].id == "test-model-002"

    async def test_ollama_cloud_routes_through_api_tags(self) -> None:
        """``ollama-cloud`` must hit ``/api/tags`` (not ``/models``).

        Pre-fix, the cloud variant fell through to ``_discover_standard_api``
        which fetched ``GET {base}/models``; Ollama returns 404 there,
        the response was treated as an empty model list, and
        ``replace_existing=True`` then deleted every persisted model
        on the first sync click.
        """
        response = _mock_response({"models": [{"name": "cloud-model-001"}]})
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            client = _mock_client(response)
            mock_cls.return_value = client

            result = await discover_models(
                "http://localhost:11434",
                "ollama-cloud",
            )

            client.get.assert_called_once_with(
                "http://127.0.0.1:11434/api/tags",
                headers={"Host": "localhost"},
            )
        assert len(result) == 1
        assert result[0].id == "cloud-model-001"


@pytest.mark.usefixtures("_bypass_ssrf")
class TestDiscoverStandardApi:
    """Tests for standard /models endpoint discovery (LM Studio, vLLM)."""

    async def test_parses_response(self) -> None:
        response = _mock_response(
            {
                "data": [
                    {"id": "model-a"},
                    {"id": "model-b"},
                ],
            }
        )
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            result = await discover_models(
                "http://localhost:1234/v1",
                "lm-studio",
            )

        assert len(result) == 2
        assert result[0].id == "model-a"
        assert result[1].id == "model-b"

    async def test_uses_models_endpoint(self) -> None:
        response = _mock_response({"data": []})
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            client = _mock_client(response)
            mock_cls.return_value = client

            await discover_models(
                "http://localhost:1234/v1",
                "lm-studio",
            )

            client.get.assert_called_once_with(
                "http://127.0.0.1:1234/v1/models",
                headers={"Host": "localhost"},
            )

    async def test_unknown_preset_uses_standard_endpoint(self) -> None:
        response = _mock_response({"data": []})
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            client = _mock_client(response)
            mock_cls.return_value = client

            await discover_models(
                "http://localhost:9999",
                None,
            )

            client.get.assert_called_once_with(
                "http://127.0.0.1:9999/models",
                headers={"Host": "localhost"},
            )

    async def test_malformed_json(self) -> None:
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            bad_response = httpx.Response(
                status_code=200,
                content=b"not json",
                request=httpx.Request("GET", "http://test"),
            )
            mock_cls.return_value = _mock_client(bad_response)

            result = await discover_models(
                "http://localhost:1234/v1",
                "lm-studio",
            )

        assert result == ()

    async def test_http_error(self) -> None:
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            error_response = httpx.Response(
                status_code=500,
                request=httpx.Request("GET", "http://test"),
            )
            mock_cls.return_value = _mock_client(error_response)

            result = await discover_models(
                "http://localhost:1234/v1",
                "vllm",
            )

        assert result == ()

    async def test_malformed_entries_skipped(self) -> None:
        """Valid models returned even when some entries are malformed."""
        response = _mock_response(
            {
                "data": [
                    {"id": "valid"},
                    42,
                    {"id": "  "},
                    {"id": "also-valid"},
                ],
            }
        )
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            result = await discover_models(
                "http://localhost:1234/v1",
                "lm-studio",
            )

        assert len(result) == 2
        assert result[0].id == "valid"
        assert result[1].id == "also-valid"

    async def test_non_dict_json_returns_empty(self) -> None:
        """JSON array response (not a dict) returns empty tuple."""
        response = _mock_response([{"id": "model-a"}])
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            result = await discover_models(
                "http://localhost:1234/v1",
                "lm-studio",
            )

        assert result == ()


def _fake_getaddrinfo(
    host: str,
    _port: object,
    *_args: object,
    **_kwargs: object,
) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    """Deterministic DNS resolution for SSRF tests."""
    if host == "localhost":
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
    # All other hostnames resolve to a safe public IP.
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


class TestValidateDiscoveryUrl:
    """Tests for SSRF URL validation."""

    @pytest.fixture(autouse=True)
    def _mock_dns(self) -> Generator[None]:
        """Provide deterministic DNS for URL validation tests."""
        with patch(
            "synthorg.providers.discovery.socket.getaddrinfo",
            side_effect=_fake_getaddrinfo,
        ):
            yield

    @pytest.mark.parametrize(
        ("url", "expected_safe"),
        [
            ("http://localhost:11434", False),
            ("https://api.example.com/v1", True),
            ("http://192.168.1.1:11434", False),
            ("http://10.0.0.1:8000", False),
            ("http://127.0.0.1:11434", False),
            ("http://169.254.169.254/latest", False),
            ("ftp://example.com", False),
            ("file:///etc/passwd", False),
            ("http://172.16.0.1:8000", False),
            # IPv6-mapped IPv4 addresses.
            ("http://[::ffff:127.0.0.1]:11434", False),
            ("http://[::ffff:10.0.0.1]:8080", False),
            ("http://[::ffff:8.8.8.8]:8080", True),
            # Edge cases.
            ("http:///path", False),
            ("data:text/plain,hello", False),
            ("http://user@example.com:8080/", True),
        ],
    )
    async def test_url_validation(self, url: str, *, expected_safe: bool) -> None:
        result = await _validate_discovery_url(url)
        if expected_safe:
            assert result.error is None, (
                f"Expected {url} to be safe, got: {result.error}"
            )
            assert result.pinned_ip is not None, f"Expected {url} to return a pinned IP"
        else:
            assert result.error is not None, f"Expected {url} to be blocked"

    async def test_blocked_url_returns_empty(self) -> None:
        """SSRF-blocked URL returns empty tuple without making HTTP call."""
        result = await discover_models(
            "http://169.254.169.254/latest",
            "ollama",
        )
        assert result == ()


@pytest.mark.usefixtures("_bypass_ssrf")
class TestDiscoverModelsRedirect:
    """Tests for redirect-following behavior."""

    async def test_redirect_not_followed(self) -> None:
        """Discovery returns empty tuple when server responds with redirect."""
        redirect_response = httpx.Response(
            status_code=302,
            headers={"Location": "http://evil.example.com/models"},
            request=httpx.Request("GET", "http://safe.example.com:1234/models"),
        )
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            client = _mock_client(redirect_response)
            mock_cls.return_value = client

            result = await discover_models(
                "http://safe.example.com:1234",
                None,
            )

        # With follow_redirects=False, the 302 response is not
        # followed and cannot be parsed as JSON, so discover_models
        # returns an empty tuple instead of following the redirect.
        assert result == ()


class TestInferPresetHint:
    """Tests for _infer_preset_hint port-based heuristic."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("http://localhost:11434", "ollama"),
            ("http://localhost:1234/v1", "lm-studio"),
            ("http://localhost:8000", None),
            ("http://localhost:9999", None),
            ("http://example.com", None),
            ("http://localhost:11434/api", "ollama"),
        ],
    )
    def test_port_mapping(self, url: str, expected: str | None) -> None:
        from synthorg.providers.management._helpers import infer_preset_hint

        assert infer_preset_hint(url) == expected


class TestProbePresetUrls:
    """Tests for probe_preset_urls candidate URL probing."""

    async def test_returns_first_reachable_url(self) -> None:
        """First reachable candidate wins."""
        ollama_response = _mock_response(
            {"models": [{"name": "llama3"}]},
        )
        client = _mock_client(ollama_response)
        fake_preset = Mock(
            spec=LocalPreset,
            candidate_urls=(
                "http://host.docker.internal:11434",
                "http://localhost:11434",
            ),
        )

        with (
            patch(
                "synthorg.providers.presets.get_preset",
                return_value=fake_preset,
            ),
            patch(
                "synthorg.providers.probing.httpx.AsyncClient",
                return_value=client,
            ),
        ):
            result = await probe_preset_urls("ollama")
        assert result.url == "http://host.docker.internal:11434"
        assert result.model_count == 1
        assert result.candidates_tried == 1

    async def test_skips_unreachable_tries_next(self) -> None:
        """Unreachable URL is skipped, next one is tried."""
        ok_response = _mock_response(
            {"models": [{"name": "phi3"}, {"name": "llama3"}]},
        )

        async def side_effect_get(url: str, **kwargs: Any) -> httpx.Response:
            if "host.docker.internal" in url:
                msg = "refused"
                raise httpx.ConnectError(msg)
            return ok_response

        client = _mock_client(ok_response)
        client.get.side_effect = side_effect_get
        fake_preset = Mock(
            spec=LocalPreset,
            candidate_urls=(
                "http://host.docker.internal:11434",
                "http://172.17.0.1:11434",
            ),
        )

        with (
            patch(
                "synthorg.providers.presets.get_preset",
                return_value=fake_preset,
            ),
            patch(
                "synthorg.providers.probing.httpx.AsyncClient",
                return_value=client,
            ),
        ):
            result = await probe_preset_urls("ollama")
        assert result.url == "http://172.17.0.1:11434"
        assert result.model_count == 2
        assert result.candidates_tried == 2

    async def test_all_unreachable_returns_empty(self) -> None:
        """When all candidates fail, returns empty result."""
        client = _mock_client(side_effect=httpx.ConnectError("refused"))
        fake_preset = Mock(
            spec=LocalPreset,
            candidate_urls=("http://a:11434", "http://b:11434"),
        )

        with (
            patch(
                "synthorg.providers.presets.get_preset",
                return_value=fake_preset,
            ),
            patch(
                "synthorg.providers.probing.httpx.AsyncClient",
                return_value=client,
            ),
        ):
            result = await probe_preset_urls("ollama")
        assert result.url is None
        assert result.model_count == 0
        assert result.candidates_tried == 2

    async def test_empty_candidates(self) -> None:
        """No candidates to probe returns empty result."""
        fake_preset = Mock(spec=LocalPreset, candidate_urls=())

        with patch(
            "synthorg.providers.presets.get_preset",
            return_value=fake_preset,
        ):
            result = await probe_preset_urls("ollama")
        assert result == ProbeResult(candidates_tried=0)

    async def test_standard_api_probe(self) -> None:
        """Standard API presets probe /models endpoint."""
        response = _mock_response(
            {"data": [{"id": "model-a"}, {"id": "model-b"}]},
        )
        client = _mock_client(response)
        fake_preset = Mock(
            spec=LocalPreset,
            candidate_urls=(
                "http://host.docker.internal:1234/v1",
                "http://172.17.0.1:1234/v1",
                "http://localhost:1234/v1",
            ),
        )

        with (
            patch(
                "synthorg.providers.presets.get_preset",
                return_value=fake_preset,
            ),
            patch(
                "synthorg.providers.probing.httpx.AsyncClient",
                return_value=client,
            ),
        ):
            result = await probe_preset_urls("lm-studio")
        assert result.url == "http://host.docker.internal:1234/v1"
        assert result.model_count == 2
        assert result.candidates_tried == 1

    async def test_probe_timeout_skips_url(self) -> None:
        """Timeout is handled gracefully and URL is skipped."""
        client = _mock_client(side_effect=httpx.TimeoutException("timed out"))
        fake_preset = Mock(
            spec=LocalPreset,
            candidate_urls=("http://slow-host:11434",),
        )

        with (
            patch(
                "synthorg.providers.presets.get_preset",
                return_value=fake_preset,
            ),
            patch(
                "synthorg.providers.probing.httpx.AsyncClient",
                return_value=client,
            ),
        ):
            result = await probe_preset_urls("ollama")
        assert result.url is None
        assert result.candidates_tried == 1

    async def test_probe_non_2xx_skips_url(self) -> None:
        """Non-2xx response is treated as a miss."""
        response = _mock_response({"error": "not found"}, status_code=404)
        client = _mock_client(response)
        fake_preset = Mock(
            spec=LocalPreset,
            candidate_urls=("http://host:11434",),
        )

        with (
            patch(
                "synthorg.providers.presets.get_preset",
                return_value=fake_preset,
            ),
            patch(
                "synthorg.providers.probing.httpx.AsyncClient",
                return_value=client,
            ),
        ):
            result = await probe_preset_urls("ollama")
        assert result.url is None
        assert result.candidates_tried == 1

    async def test_probe_json_decode_error_skips_url(self) -> None:
        """Non-JSON 200 response is treated as a miss."""
        # Real httpx.Response with non-JSON body -- .json() raises
        # JSONDecodeError exactly like production.
        html_response = httpx.Response(
            status_code=200,
            content=b"<html>not json</html>",
            request=httpx.Request("GET", "http://test"),
        )
        client = _mock_client(html_response)
        fake_preset = Mock(
            spec=LocalPreset,
            candidate_urls=("http://host:11434",),
        )

        with (
            patch(
                "synthorg.providers.presets.get_preset",
                return_value=fake_preset,
            ),
            patch(
                "synthorg.providers.probing.httpx.AsyncClient",
                return_value=client,
            ),
        ):
            result = await probe_preset_urls("ollama")
        assert result.url is None
        assert result.candidates_tried == 1

    async def test_probe_non_dict_json_skips_url(self) -> None:
        """JSON array response is treated as a miss."""
        response = _mock_response([{"not": "a dict"}])
        client = _mock_client(response)
        fake_preset = Mock(
            spec=LocalPreset,
            candidate_urls=("http://host:11434",),
        )

        with (
            patch(
                "synthorg.providers.presets.get_preset",
                return_value=fake_preset,
            ),
            patch(
                "synthorg.providers.probing.httpx.AsyncClient",
                return_value=client,
            ),
        ):
            result = await probe_preset_urls("ollama")
        assert result.url is None
        assert result.candidates_tried == 1


class TestDiscoverModelsTrustedUrl:
    """Tests for discover_models with trust_url=True (SSRF bypass)."""

    async def test_trusted_url_skips_ssrf_validation(self) -> None:
        """trust_url=True bypasses SSRF validation entirely."""
        response = _mock_response(
            {"data": [{"id": "test-model-001"}]},
        )
        with (
            patch(
                "synthorg.providers.discovery.httpx.AsyncClient",
            ) as mock_cls,
            patch(
                "synthorg.providers.discovery._validate_discovery_url",
            ) as mock_ssrf,
        ):
            mock_cls.return_value = _mock_client(response)

            result = await discover_models(
                "http://localhost:1234/v1",
                "lm-studio",
                trust_url=True,
            )

        # SSRF validation must NOT have been called.
        mock_ssrf.assert_not_called()
        assert len(result) == 1
        assert result[0].id == "test-model-001"

    async def test_trusted_url_uses_original_url(self) -> None:
        """trust_url=True sends the request to the original URL (no IP pinning)."""
        response = _mock_response(
            {"data": [{"id": "test-model-001"}]},
        )
        with patch(
            "synthorg.providers.discovery.httpx.AsyncClient",
        ) as mock_cls:
            client = _mock_client(response)
            mock_cls.return_value = client

            await discover_models(
                "http://localhost:1234/v1",
                "lm-studio",
                trust_url=True,
            )

            # The request should go to the original URL, not an IP-pinned one.
            client.get.assert_called_once()
            call_args = client.get.call_args
            url = call_args[0][0]
            assert "localhost" in url
            # No Host header rewriting when trusted.
            headers = call_args[1].get("headers", call_args.kwargs.get("headers", {}))
            assert "Host" not in headers

    async def test_trusted_url_logs_ssrf_bypass(self) -> None:
        """trust_url=True logs the SSRF bypass event."""
        response = _mock_response(
            {"data": [{"id": "test-model-001"}]},
        )
        with (
            patch(
                "synthorg.providers.discovery.httpx.AsyncClient",
            ) as mock_cls,
            patch(
                "synthorg.providers.discovery.logger",
            ) as mock_logger,
        ):
            mock_cls.return_value = _mock_client(response)

            await discover_models(
                "http://localhost:1234/v1",
                "lm-studio",
                trust_url=True,
            )

        # Verify the SSRF bypass event was logged.
        from synthorg.observability.events.provider import (
            PROVIDER_DISCOVERY_SSRF_BYPASSED,
        )

        mock_logger.warning.assert_any_call(
            PROVIDER_DISCOVERY_SSRF_BYPASSED,
            preset="lm-studio",
            url="http://localhost:1234/v1/models",
        )
