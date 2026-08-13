"""Tests for provider model auto-discovery."""

import socket
from collections.abc import Generator
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers._discovery_ssrf import (
    SsrfCheckResult,
    validate_discovery_url,
)
from synthorg.providers.discovery import discover_models
from synthorg.providers.errors import (
    AuthenticationError,
    InvalidRequestError,
    ProviderConnectionError,
    ProviderError,
    ProviderInternalError,
    ProviderTimeoutError,
    RateLimitError,
)
from synthorg.providers.presets import LocalPreset
from synthorg.providers.probing import (
    ProbeResult,
    probe_preset_urls,
)

pytestmark = pytest.mark.unit


def _mock_response(json_data: object, status_code: int = 200) -> httpx.Response:
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
    safe_result = SsrfCheckResult(error=None, pinned_ip="127.0.0.1")
    with patch(
        "synthorg.providers.discovery.validate_discovery_url",
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
                    {"name": "test-expert-001:latest"},
                    {"name": "test-basic-001:7b"},
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
        assert result[0].id == "test-expert-001:latest"
        assert result[1].id == "test-basic-001:7b"

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

    async def test_ollama_cloud_routes_through_openai_models(self) -> None:
        """``ollama-cloud`` lists via the OpenAI-compatible ``/v1/models``.

        Ollama Cloud is reached through its OpenAI-compatible endpoint
        (``https://ollama.com/v1``), so discovery hits ``GET {base}/models``
        (the standard path) rather than the native local ``/api/tags``.
        """
        response = _mock_response({"data": [{"id": "cloud-model-001"}]})
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            client = _mock_client(response)
            mock_cls.return_value = client

            result = await discover_models(
                "http://localhost:11434/v1",
                "ollama-cloud",
            )

            # Lists via the OpenAI /v1/models path (not native /api/tags).
            # Capability enrichment additionally probes /api/version to detect
            # whether the endpoint speaks the native Ollama API.
            client.get.assert_any_call(
                "http://127.0.0.1:11434/v1/models",
                headers={"Host": "localhost"},
            )
            called_urls = [call.args[0] for call in client.get.call_args_list]
            assert not any("/api/tags" in url for url in called_urls)
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

    async def test_carries_provider_context_window(self) -> None:
        """The provider's per-model ``max_input_tokens`` becomes ``max_context``.

        An OpenAI-compatible listing is the provider's own catalogue, so a
        context window it reports is authoritative and must be carried through
        rather than discarded in favour of LiteLLM's static database. An entry
        that omits it (or reports a non-positive value) leaves the field at its
        default so downstream enrichment can still fill it.
        """
        response = _mock_response(
            {
                "data": [
                    {"id": "with-ctx", "max_input_tokens": 314_159},
                    {"id": "no-ctx"},
                    {"id": "zero-ctx", "max_input_tokens": 0},
                    {"id": "neg-ctx", "max_input_tokens": -5},
                    # ``True`` is an ``int`` subclass; it must not masquerade as 1.
                    {"id": "bool-ctx", "max_input_tokens": True},
                    # An untrusted gateway must not inflate the window past the
                    # sanity ceiling to skew model selection.
                    {"id": "huge-ctx", "max_input_tokens": 999_999_999_999},
                ],
            }
        )
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            result = await discover_models("http://localhost:1234/v1", "lm-studio")

        by_id = {model.id: model for model in result}
        default_ctx = ProviderModelConfig(id="x").max_context
        # The reported window is carried through verbatim.
        assert by_id["with-ctx"].max_context == 314_159
        assert by_id["with-ctx"].max_context != default_ctx
        # Absent / non-positive / bool / implausibly-large windows all fall back
        # to the shared default rather than trusting a garbage value.
        for garbage_id in ("no-ctx", "zero-ctx", "neg-ctx", "bool-ctx", "huge-ctx"):
            assert by_id[garbage_id].max_context == default_ctx

    async def test_strict_raises_typed_error_on_http_failure(self) -> None:
        """A strict discovery surfaces a failed round-trip as a typed error.

        On a provider save for a live-discovery gateway, discovery is
        authoritative: a 429 must raise (so the operator sees the real
        reason) rather than degrade to an empty tuple that a caller could
        mistake for a genuinely empty catalogue.
        """
        response = _mock_response({"error": "slow down"}, status_code=429)
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            with pytest.raises(RateLimitError):
                await discover_models(
                    "http://localhost:1234/v1", "lm-studio", strict=True
                )

    async def test_non_strict_degrades_to_empty_on_http_failure(self) -> None:
        """Without ``strict`` a failed round-trip still degrades to empty."""
        response = _mock_response({"error": "slow down"}, status_code=429)
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            result = await discover_models("http://localhost:1234/v1", "lm-studio")

        assert result == ()

    @pytest.mark.parametrize(
        ("status_code", "expected"),
        [
            (401, AuthenticationError),
            (403, AuthenticationError),
            (500, ProviderInternalError),
            (503, ProviderInternalError),
            (400, InvalidRequestError),
            (404, InvalidRequestError),
        ],
    )
    async def test_strict_maps_http_status_to_typed_error(
        self, status_code: int, expected: type[ProviderError]
    ) -> None:
        """Each HTTP status class maps to its distinct typed provider error.

        A status-comparison bug (e.g. an off-by-one on the 5xx floor, or 403
        falling through to the generic branch) would misclassify an auth
        failure as a generic invalid-request, so the operator's remedy differs;
        each class is asserted independently.
        """
        response = _mock_response({"error": "x"}, status_code=status_code)
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            with pytest.raises(expected):
                await discover_models(
                    "http://localhost:1234/v1", "lm-studio", strict=True
                )

    @pytest.mark.parametrize(
        ("side_effect", "expected"),
        [
            (httpx.ConnectError("refused"), ProviderConnectionError),
            (httpx.TimeoutException("slow"), ProviderTimeoutError),
        ],
    )
    async def test_strict_maps_transport_error_to_typed_error(
        self, side_effect: Exception, expected: type[ProviderError]
    ) -> None:
        """A strict connect / timeout failure raises its typed provider error."""
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(side_effect=side_effect)

            with pytest.raises(expected):
                await discover_models(
                    "http://localhost:1234/v1", "lm-studio", strict=True
                )

    async def test_strict_raises_on_non_json_response(self) -> None:
        """A strict discovery raises when the body is not JSON."""
        response = httpx.Response(
            status_code=200,
            content=b"<html>not json</html>",
            request=httpx.Request("GET", "http://test"),
        )
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            with pytest.raises(ProviderInternalError):
                await discover_models(
                    "http://localhost:1234/v1", "lm-studio", strict=True
                )

    async def test_strict_raises_on_ssrf_rejection(self) -> None:
        """A strict discovery surfaces an SSRF rejection as an invalid request."""
        blocked = SsrfCheckResult(error="blocked private ip", pinned_ip=None)
        with (
            patch(
                "synthorg.providers.discovery.validate_discovery_url",
                return_value=blocked,
            ),
            pytest.raises(InvalidRequestError),
        ):
            await discover_models("http://169.254.169.254/v1", "lm-studio", strict=True)

    async def test_strict_raises_on_non_dict_body(self) -> None:
        """A strict discovery raises when a 200 body is valid JSON but not a dict.

        A malformed-but-successful response must not slip through strict mode
        as an empty catalogue -- it surfaces as a typed provider error.
        """
        response = _mock_response(["not", "a", "dict"], status_code=200)
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            with pytest.raises(ProviderInternalError):
                await discover_models(
                    "http://localhost:1234/v1", "lm-studio", strict=True
                )

    async def test_strict_raises_on_unexpected_shape(self) -> None:
        """A strict discovery raises when the dict lacks the model listing."""
        response = _mock_response({"unexpected": "shape"}, status_code=200)
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            with pytest.raises(ProviderInternalError):
                await discover_models(
                    "http://localhost:1234/v1", "lm-studio", strict=True
                )

    async def test_non_strict_degrades_to_empty_on_unexpected_shape(self) -> None:
        """Without ``strict`` an unexpected-shape body still degrades to empty."""
        response = _mock_response({"unexpected": "shape"}, status_code=200)
        with patch("synthorg.providers.discovery.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(response)

            result = await discover_models("http://localhost:1234/v1", "lm-studio")

        assert result == ()

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
            "synthorg.providers._discovery_ssrf.socket.getaddrinfo",
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
        result = await validate_discovery_url(url)
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
        from synthorg.providers.management._discovery_auth import infer_preset_hint

        assert infer_preset_hint(url) == expected


class TestResolveDiscoveryHint:
    """Tests for the discovery-hint resolution precedence."""

    @staticmethod
    def _config(*, preset_name: str | None, base_url: str | None) -> ProviderConfig:
        return ProviderConfig(
            connection_name="conn-test",
            base_url=base_url,
            preset_name=preset_name,
        )

    def test_explicit_hint_wins(self) -> None:
        from synthorg.providers.management._discovery_auth import resolve_discovery_hint

        config = self._config(preset_name="ollama-cloud", base_url="http://x:11434")
        assert resolve_discovery_hint(config, "override") == "override"

    def test_stored_preset_preferred_over_url_inference(self) -> None:
        from synthorg.providers.management._discovery_auth import resolve_discovery_hint

        # ollama.com exposes no port; without the preset preference this would
        # degrade to the generic parser.
        config = self._config(
            preset_name="ollama-cloud", base_url="https://ollama.com/v1"
        )
        assert resolve_discovery_hint(config, None) == "ollama-cloud"

    def test_falls_back_to_url_heuristic(self) -> None:
        from synthorg.providers.management._discovery_auth import resolve_discovery_hint

        config = self._config(preset_name=None, base_url="http://localhost:11434")
        assert resolve_discovery_hint(config, None) == "ollama"

    def test_none_when_nothing_resolves(self) -> None:
        from synthorg.providers.management._discovery_auth import resolve_discovery_hint

        config = self._config(preset_name=None, base_url="https://ollama.com/v1")
        assert resolve_discovery_hint(config, None) is None


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

        async def side_effect_get(url: str, **kwargs: object) -> httpx.Response:
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
                "synthorg.providers.discovery.validate_discovery_url",
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

            # Every request (the /models list + the capability-enrichment
            # /api/version probe) goes to the original URL, not an IP-pinned
            # one, and carries no rewritten Host header.
            assert client.get.call_count >= 1
            for call in client.get.call_args_list:
                assert "localhost" in call.args[0]
                assert "Host" not in (call.kwargs.get("headers") or {})

    async def test_trusted_url_logs_ssrf_bypass_at_debug(self) -> None:
        """trust_url=True logs the allowlisted-fetch event at DEBUG.

        A trusted discovery URL is auto-allowlisted (preset candidate or
        admin-entered provider base), so fetching it is a legitimate call,
        not a security event: it logs at DEBUG, never WARNING.
        """
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

        from synthorg.observability.events.provider import (
            PROVIDER_DISCOVERY_SSRF_BYPASSED,
        )

        mock_logger.debug.assert_any_call(
            PROVIDER_DISCOVERY_SSRF_BYPASSED,
            preset="lm-studio",
            url="http://localhost:1234/v1/models",
        )
        bypass_warnings = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args and call.args[0] == PROVIDER_DISCOVERY_SSRF_BYPASSED
        ]
        assert bypass_warnings == []

    async def test_ssrf_bypass_never_warns_across_passes(self) -> None:
        """Every allowlisted fetch, in any pass, stays on the DEBUG channel.

        A discovery pass fans out several fetches against the same origin
        (listing + capability probes) and passes recur; none of them is a
        security event, so none warns.
        """
        from synthorg.observability.events.provider import (
            PROVIDER_DISCOVERY_SSRF_BYPASSED,
        )

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
            await discover_models(
                "http://localhost:1234/v1",
                "lm-studio",
                trust_url=True,
            )

        bypass_warnings = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args and call.args[0] == PROVIDER_DISCOVERY_SSRF_BYPASSED
        ]
        bypass_debugs = [
            call
            for call in mock_logger.debug.call_args_list
            if call.args and call.args[0] == PROVIDER_DISCOVERY_SSRF_BYPASSED
        ]
        assert bypass_warnings == []
        assert len(bypass_debugs) >= 2
